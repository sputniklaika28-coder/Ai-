# mypy: ignore-errors
# ================================
# ファイル: core/ccfolia_connector.py
# CCFolia連携 - チャット監視 + 自動投稿 + セッション記録 + エージェント機能
#
# リファクタ版: Selenium を排除し VTTアダプター（Playwright）に委譲。
# KnowledgeManager による RAG/Web検索ツールを統合。
# ================================

from __future__ import annotations

import asyncio
import base64
import json
import logging
import queue
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

# stdout をバッファリングなしにする（クラッシュ時にもログが表示されるように）
try:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
except Exception:
    pass

# 同階層モジュール + リポジトリルートをパスに追加
_CORE_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _CORE_DIR.parent
sys.path.insert(0, str(_CORE_DIR))
sys.path.insert(0, str(_ROOT_DIR))
from ccfolia_map_controller import MAP_TOOLS, CCFoliaMapController, execute_map_tool
from character_manager import CharacterManager
from knowledge_manager import KnowledgeManager
from lm_client import LMClient
from main import PromptManager
from memory_manager import MemoryManager
from session_manager import SessionManager
from vtt_adapters.ccfolia_adapter import CCFoliaAdapter

# アドオンフレームワーク
sys.path.insert(0, str(_ROOT_DIR))
from core.addons import AddonContext, AddonManager, ToolExecutionContext

logger = logging.getLogger(__name__)


# ==========================================
# エージェント専用 ツール定義
# ==========================================

AGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "post_chat",
            "description": "CCFoliaに発言や情景描写を投稿する。",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "投稿するテキスト"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "手番を終了する。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ==========================================
# ナレッジ検索ツール定義
# ==========================================

KNOWLEDGE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "ルールブックやセッションログをベクトル検索する",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "インターネットで情報を検索する",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"},
                },
                "required": ["query"],
            },
        },
    },
]

# ==========================================
# Phase 2: アセット・Vision ツール定義
# ==========================================

ASSET_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "upload_asset",
            "description": "ローカルファイルをCCFoliaにアップロードする（画像・BGM）",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "アップロードするファイルパス"},
                    "asset_type": {
                        "type": "string",
                        "enum": ["background", "token", "bgm"],
                        "description": "アセット種別",
                    },
                },
                "required": ["file_path", "asset_type"],
            },
        },
    },
]

VISION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "analyze_board_vision",
            "description": "VLMで盤面を視覚的に解析し、Canvas上の駒や地形を検出する",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "解析の焦点（省略可）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_piece_at_location",
            "description": "自然言語で指定した位置にコマを配置する（VLMで座標を特定）",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "配置位置の説明（例: 十字路、部屋の中央）"},
                    "character_json": {
                        "type": "object",
                        "description": "CCFolia形式のキャラクターデータ",
                    },
                },
                "required": ["description", "character_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_board_scene",
            "description": "VLMで現在の盤面状態を自然言語で説明する",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

ROOM_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "build_room",
            "description": "構造化定義からCCFoliaルームを自動構築する（背景・BGM・キャラクター一括配置）",
            "parameters": {
                "type": "object",
                "properties": {
                    "room_definition": {
                        "type": "object",
                        "description": "ルーム定義（name, background_image, bgm, characters）",
                    },
                },
                "required": ["room_definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_room_background",
            "description": "現在のルームの背景画像をアップロードして設定する",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "背景画像のローカルパス"},
                },
                "required": ["image_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_room_bgm",
            "description": "ルームにBGMを追加する",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "音声ファイルパス"},
                    "name": {"type": "string", "description": "BGM名"},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_room_character",
            "description": "ルームにキャラクターを配置する",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "キャラクター名"},
                    "position": {"type": "string", "description": "配置位置の説明（自然言語）"},
                    "grid_x": {"type": "integer", "description": "グリッドX座標（省略可）"},
                    "grid_y": {"type": "integer", "description": "グリッドY座標（省略可）"},
                    "ccfolia_data": {"type": "object", "description": "CCFolia形式キャラクターデータ"},
                },
                "required": ["name", "ccfolia_data"],
            },
        },
    },
]

COPILOT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "transition_scene",
            "description": "登録済みシーンに遷移する（背景・BGM・キャラクター一括変更）",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_name": {"type": "string", "description": "遷移先のシーン名"},
                },
                "required": ["scene_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scenes",
            "description": "登録済みシーンの一覧を取得する",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_scene",
            "description": "新しいシーンを登録する",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_definition": {
                        "type": "object",
                        "description": "シーン定義（name, background_image, bgm, characters）",
                    },
                },
                "required": ["scene_definition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_copilot_mode",
            "description": "コパイロットのモードを切り替える（auto: 自動実行, assist: 提案のみ）",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["auto", "assist"]},
                },
                "required": ["mode"],
            },
        },
    },
]

HEALTH_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_system_health",
            "description": "システム稼働状態を確認する（VTT接続・LM・ビルド状態）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

BUILD_MODE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "enter_build_mode",
            "description": "ビルドモードに入る（RP停止、部屋構築専念）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_build_mode",
            "description": "ビルドモードを終了する（RP再開）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# 全ツール結合
ALL_TOOLS: list[dict] = (
    AGENT_TOOLS + MAP_TOOLS + KNOWLEDGE_TOOLS
    + ASSET_TOOLS + VISION_TOOLS + ROOM_TOOLS + COPILOT_TOOLS
    + HEALTH_TOOLS + BUILD_MODE_TOOLS
)


# ==========================================
# ビルドモード・システムヘルス
# ==========================================


@dataclass
class BuildModeStatus:
    """ビルドモードの状態管理。ビルド中はRP機能を停止する。"""

    is_active: bool = False
    current_step: str = ""
    completed_steps: int = 0
    total_steps: int = 0
    errors: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.is_active = False
        self.current_step = ""
        self.completed_steps = 0
        self.total_steps = 0
        self.errors = []


@dataclass
class SystemHealthStatus:
    """システム全体の稼働状態。"""

    vtt_connected: bool = False
    vtt_mode: str = "disconnected"  # "disconnected" | "cdp" | "playwright" | "browser_use"
    lm_reachable: bool = False
    build_mode: str = "idle"  # "idle" | "building"
    room_url: str = ""

    def to_display(self) -> str:
        ok, ng = "○", "×"
        return (
            f"VTT: {ok if self.vtt_connected else ng} ({self.vtt_mode}) | "
            f"LM: {ok if self.lm_reachable else ng} | "
            f"ビルド: {self.build_mode}"
        )

    def to_dict(self) -> dict:
        return {
            "vtt_connected": self.vtt_connected,
            "vtt_mode": self.vtt_mode,
            "lm_reachable": self.lm_reachable,
            "build_mode": self.build_mode,
            "room_url": self.room_url,
        }


# ==========================================
# キャラクター判定ロジック
# ==========================================


class CharacterDetector:
    def __init__(self, character_manager: CharacterManager, default_id: str = "meta_gm"):
        self.cm = character_manager
        self.default_id = default_id
        self._build_keyword_map()

    def _build_keyword_map(self) -> None:
        self.keyword_map: dict[str, list[str]] = {}
        for char_id, char in self.cm.characters.items():
            if not char.get("is_ai") or not char.get("enabled"):
                continue
            keywords = char.get("keywords", []) or [char.get("name", ""), char_id]
            self.keyword_map[char_id] = [k for k in keywords if k]

    def detect(self, message: str) -> list[str]:
        matched_ids: list[str] = []
        for char_id, keywords in self.keyword_map.items():
            for kw in keywords:
                if kw and kw in message:
                    if char_id not in matched_ids:
                        matched_ids.append(char_id)
                    break
        return matched_ids

    def reload(self) -> None:
        self.cm.load_characters()
        self._build_keyword_map()


# ==========================================
# セッション文脈管理
# ==========================================


class SessionContext:
    _DICE_RE = re.compile(r"\d*[dDbB]\d+|b\d+", re.IGNORECASE)
    _PHASE_KEYWORDS: dict[str, list[str]] = {
        "combat": ["戦闘開始", "戦闘スタート", "エンカウント", "敵が現れ"],
        "mission": ["ミッション開始", "ミッションフェイズ", "突入"],
        "assessment": ["査定フェイズ", "帰還"],
        "briefing": ["ブリーフィング"],
    }
    _PHASE_ORDER: dict[str, int] = {
        "free": 0, "briefing": 1, "mission": 2, "combat": 3, "assessment": 4
    }

    def __init__(self, lm_client=None) -> None:
        self.phase: str = "free"
        # MemoryManager で履歴を管理（ローリング要約対応）
        self._memory = MemoryManager(lm_client=lm_client)

    def attach_lm_client(self, lm_client) -> None:
        """起動後に LMClient を注入する（遅延初期化用）。"""
        self._memory.lm_client = lm_client

    def set_phase_keywords(self, keywords: dict[str, list[str]]) -> None:
        """ルールアドオンからフェイズキーワードを動的に設定する。"""
        if keywords:
            self._PHASE_KEYWORDS = keywords

    @property
    def history(self) -> list[dict]:
        """後方互換: 直近メッセージのリストを返す。"""
        return self._memory.get_recent_messages()

    def update_phase(self, body: str, is_ai: bool = False) -> None:
        if is_ai:
            return
        new_phase = self.phase
        for phase, keywords in self._PHASE_KEYWORDS.items():
            if any(kw in body for kw in keywords):
                new_phase = phase
                break
        if self._PHASE_ORDER.get(new_phase, 0) > self._PHASE_ORDER.get(self.phase, 0):
            self.phase = new_phase

    def add_message(self, speaker: str, body: str, is_ai: bool = False) -> None:
        self._memory.add_message(speaker, body)
        self.update_phase(body, is_ai)

    def get_context_summary(self) -> str:
        ctx = self._memory.get_context_window()
        return f"【フェイズ: {self.phase.upper()}】\n{ctx}" if ctx else f"【フェイズ: {self.phase.upper()}】"


# ==========================================
# CCFolia コネクター本体
# ==========================================


class CCFoliaConnector:
    POLL_INTERVAL = 2.0
    POST_DELAY = 1.0
    AI_PREFIX = "[AI] "

    def __init__(
        self,
        room_url: str,
        default_character_id: str = "meta_gm",
        headless: bool = False,
        poll_interval: float | None = None,
        use_browser_use: bool = False,
        cdp_url: str | None = None,
    ) -> None:
        self.room_url = room_url
        self.poll_interval = poll_interval or self.POLL_INTERVAL
        self.headless = headless
        self._use_browser_use = use_browser_use
        self.cdp_url = cdp_url

        self.lm_client = LMClient()
        # 設定ファイルは常にリポジトリルート基準の絶対パスで解決する
        self.cm = CharacterManager(str(_ROOT_DIR / "configs" / "characters.json"))
        self.pm = PromptManager(str(_ROOT_DIR / "configs" / "prompts.json"))
        self.detector = CharacterDetector(self.cm, default_id=default_character_id)
        # SessionContext に LMClient を注入してローリング要約を有効化
        self.ctx = SessionContext(lm_client=self.lm_client)
        self.sm = SessionManager(_ROOT_DIR)
        self.world_setting = self._load_world_setting()

        # VTTアダプター（Playwright ベース or Browser Use ベース）
        from vtt_adapters.base_adapter import BaseVTTAdapter
        self.adapter: BaseVTTAdapter | None = None
        self.map_ctrl: CCFoliaMapController | None = None

        # KnowledgeManager（RAG + Web検索）
        self.knowledge_manager: KnowledgeManager | None = None

        self._known_messages: list[str] = []
        self._sent_bodies: set[str] = set()
        self._running = False
        self._stdin_queue: queue.Queue[dict] = queue.Queue()

        # セッション・コパイロット（Phase 4）
        self._copilot: object | None = None

        # GMDirector 直接統合（Phase 5）
        self._gm_director: object | None = None
        self._entity_tracker: object | None = None
        self._entity_path: Path | None = None

        # ビルドモード・システムヘルス
        self._build_status = BuildModeStatus()
        self._health = SystemHealthStatus(room_url=room_url)

        # アドオンマネージャー
        self.addon_manager = AddonManager(
            addons_dir=_ROOT_DIR / "addons",
            core_dir=_CORE_DIR,
        )

        # async/sync ブリッジ用の専用イベントループ（バックグラウンドスレッドで実行）
        self._async_loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(
            target=self._async_loop.run_forever, daemon=True, name="AsyncBridge"
        )
        self._async_thread.start()

    # ──────────────────────────────────────────
    # async/sync ブリッジ
    # ──────────────────────────────────────────

    def _run_async(self, coro):
        """バックグラウンドの asyncio ループでコルーチンを実行し、結果を返す。

        Playwright（非スレッドセーフ）を同期コードから安全に呼び出すための橋渡し。
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._async_loop)
        return future.result()

    # ──────────────────────────────────────────
    # 初期化 / 終了
    # ──────────────────────────────────────────

    def _init_adapter(self) -> None:
        """VTTアダプターを初期化して VTT に接続する。

        VTT_BACKEND 環境変数で接続方式を切り替える:
          foundry  → Foundry VTT REST API（ブラウザ不要）
          vision   → VLM + pyautogui 視覚制御（任意 VTT 対応）
          ccfolia  → CCFolia Playwright 操作（既存・デフォルト）
        """
        try:
            from config import get_vtt_backend, get_foundry_url, get_foundry_api_key
            from config import get_vision_vtt_window, get_vision_vtt_grid_size
            from config import get_vision_vtt_chat_region, get_vision_vtt_board_region
        except ModuleNotFoundError:
            from core.config import get_vtt_backend, get_foundry_url, get_foundry_api_key  # type: ignore
            from core.config import get_vision_vtt_window, get_vision_vtt_grid_size  # type: ignore
            from core.config import get_vision_vtt_chat_region, get_vision_vtt_board_region  # type: ignore

        vtt_backend = get_vtt_backend()

        if vtt_backend == "foundry":
            print("⏳ Foundry VTT REST API に接続しています...")
            try:
                from vtt_adapters.foundry_adapter import FoundryVTTAdapter
            except ModuleNotFoundError:
                from core.vtt_adapters.foundry_adapter import FoundryVTTAdapter  # type: ignore
            self.adapter = FoundryVTTAdapter(
                base_url=get_foundry_url(),
                api_key=get_foundry_api_key(),
            )
            self._run_async(self.adapter.connect(self.room_url))
            self._health.vtt_mode = "foundry"
            print(f"✓ Foundry VTT に接続: {get_foundry_url()}")
            self.map_ctrl = CCFoliaMapController(adapter=self.adapter)
            self._health.vtt_connected = True
            return

        if vtt_backend == "vision":
            print("⏳ VisionVTT アダプター（VLM 視覚制御）を起動しています...")
            try:
                from vtt_adapters.vision_adapter import VisionVTTAdapter
            except ModuleNotFoundError:
                from core.vtt_adapters.vision_adapter import VisionVTTAdapter  # type: ignore
            self.adapter = VisionVTTAdapter(
                lm_client=self.lm_client,
                window_title=get_vision_vtt_window(),
                grid_size=get_vision_vtt_grid_size(),
                chat_region=get_vision_vtt_chat_region(),
                board_region=get_vision_vtt_board_region(),
            )
            self._run_async(self.adapter.connect(self.room_url))
            self._health.vtt_mode = "vision"
            print(f"✓ VisionVTT 起動 (ウィンドウ: '{get_vision_vtt_window() or 'プライマリモニタ全体'}')")
            self.map_ctrl = CCFoliaMapController(adapter=self.adapter)
            self._health.vtt_connected = True
            return

        # ── ccfolia バックエンド（既存ロジック） ────────────────
        if self.cdp_url:
            # CDP接続: GMが既に開いているブラウザに接続（権限継承）
            print(f"⏳ 既存ブラウザにCDP接続しています... ({self.cdp_url})")
            self.adapter = CCFoliaAdapter()
            self._run_async(self.adapter.connect(self.room_url, cdp_url=self.cdp_url))
            self._health.vtt_mode = "cdp"
        elif self._use_browser_use:
            print("⏳ Browser Use でブラウザを起動しています...")
            try:
                from config import load_config
                from vtt_adapters.browser_use_adapter import BrowserUseVTTAdapter
            except ModuleNotFoundError:
                from core.config import load_config
                from core.vtt_adapters.browser_use_adapter import BrowserUseVTTAdapter
            cfg = load_config()
            provider = cfg.get("browser_use_provider", "local")
            api_key = ""
            if provider == "anthropic":
                api_key = cfg["anthropic_api_key"]
            elif provider == "openai":
                api_key = cfg["openai_api_key"]
            # provider == "local" の場合は api_key 不要
            self.adapter = BrowserUseVTTAdapter(
                model_name=cfg["browser_use_model"],
                api_key=api_key,
                provider=provider,
                headless=self.headless,
                lm_studio_url=cfg.get("lm_studio_url", "http://localhost:1234"),
            )
            self._health.vtt_mode = "browser_use"
        else:
            print("⏳ Playwright でブラウザを起動しています...")
            self.adapter = CCFoliaAdapter()
            self._health.vtt_mode = "playwright"

        if not self.cdp_url:
            self._run_async(self.adapter.connect(self.room_url, headless=self.headless))
        self.map_ctrl = CCFoliaMapController(adapter=self.adapter)
        self._health.vtt_connected = True
        mode = {"cdp": "CDP", "browser_use": "Browser Use", "playwright": "Playwright"}.get(
            self._health.vtt_mode, "Playwright"
        )
        print(f"✓ CCFoliaに接続 ({mode}): {self.room_url}")

    def _init_copilot(self) -> None:
        """SessionCoPilot を初期化する。"""
        try:
            from session_copilot import SessionCoPilot
            self._copilot = SessionCoPilot(adapter=self.adapter, mode="auto")
            # シーン定義ファイルがあれば読み込む
            scenes_path = self.sm.configs_dir / "scenes.json"
            if scenes_path.exists():
                count = self._copilot.load_scenes_from_file(str(scenes_path))
                print(f"✓ シーン定義を {count} 件読み込みました")
            logger.info("SessionCoPilot 初期化完了")
        except Exception as e:
            logger.warning("SessionCoPilot 初期化エラー: %s", e)

    def _init_gm_director(self) -> None:
        """GMDirector をコネクター直下に初期化し、アドオンと状態を共有する。"""
        try:
            from core.entity_tracker import EntityTracker
            from core.game_state import GameState
            from core.gm_director import GMDirector, GMDirectorConfig

            gm_addon = self._get_gm_director_addon()
            if gm_addon and getattr(gm_addon, '_entities', None):
                self._entity_tracker = gm_addon._entities
            else:
                self._entity_tracker = EntityTracker()

            game_state = self._get_game_state_from_addons()

            session_dir = getattr(self.sm, 'current_session_dir', None)
            if session_dir:
                self._entity_path = Path(session_dir) / "entities.json"

            config = GMDirectorConfig(
                auto_resolve_combat=True,
                inject_game_state=True,
                inject_entities=True,
                inject_memory=True,
                auto_extract_entities=True,
            )
            self._gm_director = GMDirector(
                lm_client=self.lm_client,
                game_state=game_state,
                entity_tracker=self._entity_tracker,
                config=config,
                memory_manager=self.ctx._memory,
            )

            if gm_addon:
                gm_addon._director = self._gm_director
                gm_addon._entities = self._entity_tracker

            logger.info("GMDirector 直接統合: 初期化完了")
        except Exception as e:
            logger.warning("GMDirector 初期化エラー: %s", e)

    def _get_gm_director_addon(self):
        """GMDirector アドオンのインスタンスを取得する。"""
        try:
            return self.addon_manager.get_addon("gm_director")
        except Exception:
            return None

    def _get_game_state_from_addons(self):
        """combat_engine アドオンの GameState を取得。なければ新規作成。"""
        from core.game_state import GameState
        try:
            combat_addon = self.addon_manager.get_addon("combat_engine")
            if combat_addon and hasattr(combat_addon, '_game_state'):
                return combat_addon._game_state
        except Exception:
            pass
        return GameState()

    def _fallback_simple_response(self, target_char: dict, enriched: str) -> None:
        """GMDirector が利用できない場合の素の LLM 応答（フォールバック）。"""
        prompt_tmpl = self.pm.get_template(target_char.get("prompt_id"))
        parts = []
        if self.world_setting.strip():
            parts.append(self.world_setting.strip())
        if prompt_tmpl and prompt_tmpl.get("system", "").strip():
            parts.append(prompt_tmpl["system"].strip())
        sys_prompt = "\n\n".join(parts)

        res, _ = self._run_async(self.lm_client.generate_response(
            system_prompt=sys_prompt,
            user_message=enriched,
            max_tokens=8192,
        ))
        if res:
            self._post_message(target_char["name"], f"[AI] {res}")
            self.ctx.add_message(target_char["name"], res, is_ai=True)
            print(f"   ✓ 応答(fallback): {res[:40]}...")

    def _init_knowledge(self) -> None:
        """KnowledgeManager を初期化し、世界観データを取り込む。"""
        try:
            self.knowledge_manager = KnowledgeManager()
            ws_path = self.sm.configs_dir / "world_setting.json"
            if ws_path.exists():
                count = self.knowledge_manager.ingest_world_setting(ws_path)
                print(f"✓ 世界観データを {count} チャンク登録しました")
        except Exception as e:
            logger.warning("KnowledgeManager 初期化エラー: %s", e)
            self.knowledge_manager = None

    def _init_addons(self) -> None:
        """アドオンを探索・ロードし、ルールシステムのフェイズキーワードを適用する。"""
        try:
            ctx = AddonContext(
                adapter=self.adapter,
                lm_client=self.lm_client,
                knowledge_manager=self.knowledge_manager,
                session_manager=self.sm,
                character_manager=self.cm,
                root_dir=_ROOT_DIR,
            )
            manifests = self.addon_manager.discover()
            if manifests:
                self.addon_manager.load_all(ctx)
                rule = self.addon_manager.get_active_rule_system()
                if rule:
                    phase_kw = rule.get_phase_keywords()
                    if phase_kw:
                        self.ctx.set_phase_keywords(phase_kw)
                    logger.info("ルールシステム適用: %s", rule.manifest.name)
                print(f"✓ {len(self.addon_manager.loaded_addons)} 個のアドオンをロードしました")
            else:
                print("ℹ アドオンが見つかりませんでした（デフォルト動作で起動）")
        except Exception as e:
            logger.warning("アドオン初期化エラー: %s", e)

    def _get_all_tools(self) -> list[dict]:
        """コアツール + アドオンツールを集約して返す。"""
        core_tools = AGENT_TOOLS + MAP_TOOLS + KNOWLEDGE_TOOLS + HEALTH_TOOLS
        addon_tools = self.addon_manager.get_all_tools()
        return core_tools + addon_tools

    def _close_adapter(self) -> None:
        """VTTアダプターを閉じる。"""
        if self.adapter:
            try:
                self._run_async(self.adapter.close())
            except Exception:
                pass
            finally:
                self.adapter = None
                self.map_ctrl = None

    # ──────────────────────────────────────────
    # ビルドモード制御
    # ──────────────────────────────────────────

    def enter_build_mode(self, char_name: str = "GM") -> None:
        """ビルドモード開始: RP機能を停止し、部屋構築に専念する。"""
        self._build_status.reset()
        self._build_status.is_active = True
        self._health.build_mode = "building"
        self._post_system_message(
            char_name, "ビルドモードに入りました。部屋構築中はRP機能を停止します。"
        )
        logger.info("ビルドモード開始")

    def exit_build_mode(self, char_name: str = "GM") -> None:
        """ビルドモード終了: RP機能を再開する。"""
        summary = self._build_summary()
        self._build_status.is_active = False
        self._health.build_mode = "idle"
        self._post_system_message(char_name, f"ビルドモード終了。{summary}")
        logger.info("ビルドモード終了: %s", summary)

    def _build_summary(self) -> str:
        s = self._build_status
        if s.errors:
            return f"完了({s.completed_steps}/{s.total_steps}ステップ)、エラー{len(s.errors)}件"
        return f"全{s.completed_steps}ステップ正常完了"

    def _check_lm_health(self) -> bool:
        """LMクライアントの到達性を確認する。"""
        try:
            res, _ = self._run_async(self.lm_client.generate_response(
                system_prompt="あなたはGMアシスタントです。",
                user_message="準備完了を一言で答えてください。",
                max_tokens=128,
            ))
            reachable = bool(res)
        except Exception:
            reachable = False
        self._health.lm_reachable = reachable
        return reachable

    # ──────────────────────────────────────────
    # チャット操作（アダプター委譲）
    # ──────────────────────────────────────────

    def _get_chat_messages(self) -> list[dict]:
        """チャットメッセージを取得する。"""
        if self.adapter is None:
            return []
        return self._run_async(self.adapter.get_chat_messages())

    def _post_message(self, character_name: str, text: str) -> bool:
        """チャットメッセージを送信する。"""
        if self.adapter is None:
            return False
        return self._run_async(self.adapter.send_chat(character_name, text))

    def _post_system_message(self, character_name: str, text: str) -> None:
        """AIプレフィックス付きのシステムメッセージを送信する。"""
        tagged = self.AI_PREFIX + text
        ok = self._post_message(character_name, tagged)
        if ok:
            self._sent_bodies.add(tagged[:80])
            self.ctx.add_message(character_name, tagged, is_ai=True)

    # ──────────────────────────────────────────
    # ツールディスパッチャー
    # ──────────────────────────────────────────

    def _execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        char_name: str,
        tool_call_id: str,
    ) -> tuple[bool, str | None]:
        """ツール呼び出しを実行し、(finished, tool_result_json) を返す。

        Returns:
            (finished, result_json): finished=True の場合ループ終了。
            result_json はツール結果のJSON文字列（メッセージ履歴に追加用）。
        """
        if tool_name == "finish":
            return True, None

        if tool_name == "post_chat":
            text = tool_args.get("text", "")
            if text:
                tagged = self.AI_PREFIX + text
                ok = self._post_message(char_name, tagged)
                if ok:
                    self._sent_bodies.add(tagged[:80])
                    self.ctx.add_message(char_name, tagged, is_ai=True)
                    print(f"      ✓ 発言: {text[:40]}...")
            return False, json.dumps({"ok": True})

        # ナレッジ検索ツール
        if tool_name == "search_knowledge_base":
            if self.knowledge_manager:
                results = self.knowledge_manager.search_knowledge_base(
                    tool_args.get("query", "")
                )
                return False, json.dumps(results, ensure_ascii=False)
            return False, json.dumps({"error": "KnowledgeManager が未初期化です"})

        if tool_name == "search_web":
            if self.knowledge_manager:
                results = self.knowledge_manager.search_web(tool_args.get("query", ""))
                return False, json.dumps(results, ensure_ascii=False)
            return False, json.dumps({"error": "KnowledgeManager が未初期化です"})

        # ヘルス・ビルドモード制御ツール（コア状態に直接触れるためコアに残す）
        if tool_name == "get_system_health":
            return False, json.dumps(self._health.to_dict(), ensure_ascii=False)

        if tool_name == "enter_build_mode":
            if self._build_status.is_active:
                return False, json.dumps({"error": "既にビルドモード中です"})
            self.enter_build_mode(char_name)
            return False, json.dumps({"ok": True, "build_mode": "building"})

        if tool_name == "exit_build_mode":
            if not self._build_status.is_active:
                return False, json.dumps({"error": "ビルドモードではありません"})
            self.exit_build_mode(char_name)
            return False, json.dumps({"ok": True, "build_mode": "idle"})

        # マップ操作ツール（コアに残す）
        if self.map_ctrl:
            result = execute_map_tool(self.map_ctrl, tool_name, tool_args)
            if "error" not in result or "未知のツール" not in result.get("error", ""):
                return False, json.dumps(result, ensure_ascii=False, default=str)

        # アドオンに委譲
        addon_ctx = ToolExecutionContext(
            char_name=char_name,
            tool_call_id=tool_call_id,
            adapter=self.adapter,
            connector=self,
        )
        return self.addon_manager.execute_tool(tool_name, tool_args, addon_ctx)

    # ──────────────────────────────────────────
    # エージェントループ
    # ──────────────────────────────────────────

    def _run_agent_loop(self, target_char: dict, target_id: str, enriched_body: str) -> None:
        """ツール呼び出し対応のエージェントループ。

        LLMがツールを要求 → Python で実行 → 結果をプロンプトに返す
        → 最終的に post_chat / finish で完了、というフローを最大3回繰り返す。
        """
        char_name = target_char["name"]
        prompt_tmpl = self.pm.get_template(target_char.get("prompt_id"))

        # ルールアドオンから世界観・プロンプトを動的取得
        rule_addon = self.addon_manager.get_active_rule_system()
        if rule_addon:
            world = rule_addon.get_world_setting() or self.world_setting
            rule_override = rule_addon.get_system_prompt_override() or ""
        else:
            world = self.world_setting
            rule_override = ""

        base_system = prompt_tmpl["system"] if prompt_tmpl else ""
        parts = [world, base_system]
        if rule_override:
            parts.append(rule_override)
        parts.append(
            "【GMアクション指示】\n"
            "あなたはGMです。画像とチャットを見て次に行うべきことを判断してください。\n"
            "1. まず `[思考]` と `[/思考]` のタグの中で、状況を分析してください。\n"
            "2. 分析が終わったら、必ず `post_chat` ツールを使ってプレイヤーに発言してください。\n"
            "3. 発言が終わったら `finish` ツールで終了してください。\n"
            "4. ルールや知識が必要なら `search_knowledge_base` や `search_web` で検索できます。\n\n"
            "【ステータス管理の絶対ルール】\n"
            "敵やPCのHP・MPなどのステータスはあなたが頭の中で計算・管理してください。"
            "ダメージや回復など、ステータスに変動があった際は、発言の末尾に必ず"
            "「(現在HP: 敵A 5/10, 敵B 10/10)」のように明記してステータス管理を行ってください。"
        )
        sys_prompt = "\n\n".join(p for p in parts if p)

        messages: list[dict] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": enriched_body},
        ]
        print(f"\n🤖 エージェントループ開始 (最大3手番): {char_name}")

        try:
            for _step in range(3):
                # スクリーンショット取得
                screenshot_b64: str | None = None
                if self.adapter:
                    try:
                        raw = self._run_async(self.adapter.take_screenshot())
                        if raw:
                            screenshot_b64 = base64.b64encode(raw).decode("ascii")
                    except Exception:
                        pass

                content, tool_calls = self._run_async(self.lm_client.generate_with_tools(
                    messages,
                    self._get_all_tools(),
                    temperature=0.7,
                    max_tokens=1500,
                    image_base64=screenshot_b64,
                ))

                if content is None and tool_calls is None:
                    print("   ⚠️ APIからの応答がありませんでした。ループを中断します。")
                    self._post_system_message(
                        char_name,
                        "（システム: 思考処理がタイムアウトしました。処理をスキップします）",
                    )
                    break

                if content and not tool_calls:
                    text = self.lm_client._clean_response(content)
                    if text:
                        self._post_message(char_name, f"[AI] {text}")
                        self.ctx.add_message(char_name, text, is_ai=True)
                        print(f"   ✓ (自動投稿): {text[:40]}...")
                    else:
                        print(
                            "   ⚠️ 有効なテキストがありませんでした。"
                            "思考ループによる自爆と判断し終了します。"
                        )
                        self._post_system_message(
                            char_name,
                            "（システム: AIが考え込んでフリーズしました。再度指示を出してあげてください）",
                        )
                    break

                if not tool_calls:
                    break

                messages.append(
                    {"role": "assistant", "content": content or "", "tool_calls": tool_calls}
                )

                finished = False
                for tc in tool_calls:
                    f_name = tc["function"]["name"]
                    f_args = (
                        json.loads(tc["function"]["arguments"])
                        if tc["function"]["arguments"]
                        else {}
                    )
                    print(f"   🛠️ ツール実行: {f_name}")

                    is_finished, result_json = self._execute_tool(
                        f_name, f_args, char_name, tc.get("id", "")
                    )

                    if is_finished:
                        finished = True
                    elif result_json:
                        # ツール結果をメッセージ履歴に追加（マルチステップ推論用）
                        messages.append({
                            "role": "tool",
                            "content": result_json,
                            "tool_call_id": tc.get("id", ""),
                        })

                if finished:
                    break
            else:
                self._post_system_message(
                    char_name,
                    "（システム: 思考ループが上限に達したため処理を中断しました。"
                    "別のアプローチで指示してください）",
                )

        except Exception as e:
            print(f"   ❌ エージェントループ内で重大なエラーが発生しました: {str(e)}")
            self._post_system_message(
                char_name, "（システム: 予期せぬエラーが発生しました。GMの処理をスキップします）"
            )

    # ──────────────────────────────────────────
    # 世界観設定読み込み
    # ──────────────────────────────────────────

    def _load_world_setting(self) -> str:
        ws_path = self.sm.configs_dir / "world_setting.json"
        if ws_path.exists():
            try:
                with open(ws_path, encoding="utf-8") as f:
                    data = json.load(f)
                return "\n".join(v for k, v in data.items() if v)
            except Exception:
                pass
        return ""

    # ──────────────────────────────────────────
    # 監視ループ
    # ──────────────────────────────────────────

    def _monitor_loop(self) -> None:
        print("👁️  チャット監視開始")
        keywords = self.detector.keyword_map.get("meta_gm", [])
        print(f"   DEBUG: トリガーキーワード = {keywords}")
        print("   DEBUG: トリガー条件 = メッセージに '＞' を含む OR キーワード一致")
        time.sleep(2)
        initial = self._get_chat_messages()
        self._known_messages = [f"{m['speaker']}|{m['body']}" for m in initial]
        print(f"   DEBUG: 既存メッセージ数={len(self._known_messages)}")

        poll_count = 0
        zero_count = 0  # メッセージ0件が連続した回数
        while self._running:
            try:
                current = self._get_chat_messages()
            except Exception as e:
                print(f"   DEBUG: メッセージ取得エラー: {e}")
                time.sleep(self.poll_interval)
                continue

            # メッセージが0件の場合の警告（最初の数回だけ表示）
            if len(current) == 0:
                zero_count += 1
                if zero_count <= 3:
                    print(f"   ⚠ メッセージが0件です(連続{zero_count}回)。"
                          "チャットに既存メッセージがあるか確認してください。")
                elif zero_count == 10:
                    print("   ⚠ メッセージ取得が継続して0件です。"
                          "CCFoliaのDOM構造が変更されている可能性があります。")
            else:
                zero_count = 0

            new_msgs = [
                m for m in current
                if f"{m['speaker']}|{m['body']}" not in self._known_messages
                and not m["body"].startswith("[AI]")
                and "[AI] " not in m["body"][:10]
            ]

            poll_count += 1
            # 最初の5回と、以降30回ごとにポーリング状態を表示
            if poll_count <= 5 or poll_count % 30 == 0:
                print(f"   DEBUG: poll#{poll_count} 取得={len(current)}件 新着={len(new_msgs)}件")

            if new_msgs:
                for msg in new_msgs:
                    speaker, body = msg["speaker"], msg["body"]
                    self._known_messages.append(f"{speaker}|{body}")
                    self.sm.log_message(speaker, body)
                    self.ctx.add_message(speaker, body)
                    print(f"\n📨 新着: [{speaker}] {body[:40]}")

                    # ビルドモード中はRP処理をスキップ
                    if self._build_status.is_active:
                        print(f"   [ビルドモード] RP処理スキップ: {body[:30]}")
                        continue

                    # コパイロットのイベントルール処理
                    if self._copilot:
                        try:
                            actions = self._copilot.process_message(speaker, body)
                            for a in actions:
                                status = "✓" if a.success else "✗"
                                print(f"   🤖 [{status}] ルール '{a.rule_name}': {a.detail or a.error}")
                        except Exception as e:
                            logger.error("コパイロット処理エラー: %s", e)

                    trigger_gt = "＞" in body
                    trigger_kw = any(k in body for k in keywords) if keywords else False
                    print(f"   DEBUG: トリガー判定: '＞'={trigger_gt}, キーワード={trigger_kw}")

                    if trigger_gt or trigger_kw:
                        target_char = self.cm.get_character("meta_gm")
                        enriched = (
                            f"{self.ctx.get_context_summary()}\n\n"
                            f"【今回反応すべき発言】\n[{speaker}]: {body}"
                        )

                        if self.ctx.phase in ["combat", "mission"]:
                            self._run_agent_loop(target_char, "meta_gm", enriched)
                        elif self._gm_director is not None:
                            # Phase 5: GMDirector で全フェーズ統合処理
                            try:
                                result = self._run_async(
                                    self._gm_director.process_turn(
                                        player_message=body,
                                        character_name=speaker,
                                        extra_context=f"【フェイズ: {self.ctx.phase.upper()}】",
                                    )
                                )
                                for line in result.vtt_chat_lines:
                                    self._post_system_message(target_char["name"], line)
                                    print(f"   ✓ GMDirector応答: {line[:40]}...")

                                if self._entity_tracker and self._entity_path:
                                    try:
                                        self._entity_tracker.save(self._entity_path)
                                    except Exception:
                                        pass
                            except Exception as e:
                                logger.error("GMDirector 処理エラー (fallback): %s", e)
                                self._fallback_simple_response(target_char, enriched)
                        else:
                            self._fallback_simple_response(target_char, enriched)

            self._known_messages = self._known_messages[-300:]
            self._drain_stdin_queue()

            # 定期ヘルスチェック（60ポーリング≒約2分ごと）
            if poll_count % 60 == 0:
                self._check_lm_health()
                print(f"   [ヘルス] {self._health.to_display()}")

            time.sleep(self.poll_interval)

    def _stdin_monitor_loop(self) -> None:
        """ランチャーからの送信命令を受け取り、キューに積む。

        Playwright はスレッドセーフでないため、実際の送信はメインスレッド
        (_monitor_loop) 側で処理する。
        """
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "quit":
                    self._running = False
                    break
                self._stdin_queue.put(data)
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"❌ 標準入力の処理エラー: {e}")

    def _drain_stdin_queue(self) -> None:
        """メインスレッドで stdin キューに溜まった命令を処理する。"""
        while not self._stdin_queue.empty():
            try:
                data = self._stdin_queue.get_nowait()
                if data.get("type") == "chat":
                    text = data.get("text", "")
                    char_name = data.get("character", "GM")
                    print(f"📥 ランチャーから送信命令を受信: {text[:20]}...")
                    self._post_message(char_name, text)
            except queue.Empty:
                break
            except Exception as e:
                print(f"❌ stdin命令の処理エラー: {e}")

    # ──────────────────────────────────────────
    # メインエントリーポイント
    # ──────────────────────────────────────────

    def start(self) -> None:
        print("=" * 50 + "\nタクティカル祓魔師 CCFolia連携\n" + "=" * 50)

        # 設定ファイルの読み込み状況を表示
        print(f"   キャラクター数: {self.cm.get_character_count()}")
        print(f"   テンプレート数: {len(self.pm.templates)}")
        if self.cm.get_character_count() == 0:
            print("   ⚠ characters.json が読み込めていません！")
            print(f"     パス: {self.cm.config_path}")
        meta_gm = self.cm.get_character("meta_gm")
        if not meta_gm:
            print("   ⚠ meta_gm キャラクターが見つかりません！")

        self.sm.start_new_session("CCFoliaSession")
        self._init_adapter()
        self._init_knowledge()
        self._init_copilot()
        self._init_addons()
        self._init_gm_director()
        self._check_lm_health()
        print(f"   [ヘルス] {self._health.to_display()}")
        self._running = True

        # stdin監視だけ別スレッド（Playwright を触らない）
        threading.Thread(target=self._stdin_monitor_loop, daemon=True).start()

        # チャット監視はメインスレッドで実行（Playwright はスレッドセーフでないため）
        try:
            self._monitor_loop()
        except KeyboardInterrupt:
            self._running = False
            print("終了します")
            self._close_adapter()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--room", required=True)
    parser.add_argument("--default", default="meta_gm")
    parser.add_argument(
        "--cdp", default=None,
        help="CDP URL で既存ブラウザに接続 (例: http://localhost:9222)",
    )
    args = parser.parse_args()
    try:
        CCFoliaConnector(args.room, args.default, cdp_url=args.cdp).start()
    except Exception:
        print("\n" + "=" * 50)
        print("❌ 致命的エラーが発生しました:")
        print("=" * 50)
        traceback.print_exc()
        print("\n上記のエラー内容を確認してください。")
        input("Enterキーで終了...")  # ウィンドウが即閉じるのを防止
