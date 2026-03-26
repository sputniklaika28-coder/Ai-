# mypy: ignore-errors
# ================================
# ファイル: core/ccfolia_connector.py
# CCFolia連携 - チャット監視 + 自動投稿 + セッション記録 + エージェント機能
#
# リファクタ版: Selenium を排除し VTTアダプター（Playwright）に委譲。
# KnowledgeManager による RAG/Web検索ツールを統合。
# ================================

from __future__ import annotations

import base64
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

# 同階層モジュール
sys.path.insert(0, str(Path(__file__).parent))
from ccfolia_map_controller import MAP_TOOLS, CCFoliaMapController, execute_map_tool
from character_manager import CharacterManager
from knowledge_manager import KnowledgeManager
from lm_client import LMClient
from main import PromptManager
from session_manager import SessionManager
from vtt_adapters.ccfolia_adapter import CCFoliaAdapter

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

# 全ツール結合
ALL_TOOLS: list[dict] = AGENT_TOOLS + MAP_TOOLS + KNOWLEDGE_TOOLS


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

    def __init__(self) -> None:
        self.phase: str = "free"
        self.history: list[dict] = []

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
        self.history.append({"speaker": speaker, "body": body})
        self.history = self.history[-100:]
        self.update_phase(body, is_ai)

    def get_context_summary(self) -> str:
        lines = [f"[{m['speaker']}]: {m['body']}" for m in self.history[-25:]]
        return f"【フェイズ: {self.phase.upper()}】\n【直近の会話】\n" + "\n".join(lines)


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
    ) -> None:
        self.room_url = room_url
        self.poll_interval = poll_interval or self.POLL_INTERVAL
        self.headless = headless

        self.lm_client = LMClient()
        self.cm = CharacterManager()
        self.pm = PromptManager()
        self.detector = CharacterDetector(self.cm, default_id=default_character_id)
        self.ctx = SessionContext()
        self.sm = SessionManager(Path(__file__).parent.parent)
        self.world_setting = self._load_world_setting()

        # VTTアダプター（Playwright ベース）
        self.adapter: CCFoliaAdapter | None = None
        self.map_ctrl: CCFoliaMapController | None = None

        # KnowledgeManager（RAG + Web検索）
        self.knowledge_manager: KnowledgeManager | None = None

        self._known_messages: list[str] = []
        self._sent_bodies: set[str] = set()
        self._running = False

    # ──────────────────────────────────────────
    # 初期化 / 終了
    # ──────────────────────────────────────────

    def _init_adapter(self) -> None:
        """VTTアダプターを初期化してCCFoliaに接続する。"""
        print("⏳ Playwright でブラウザを起動しています...")
        self.adapter = CCFoliaAdapter()
        self.adapter.connect(self.room_url, headless=self.headless)
        self.map_ctrl = CCFoliaMapController(adapter=self.adapter)
        print(f"✓ CCFoliaに接続: {self.room_url}")

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

    def _close_adapter(self) -> None:
        """VTTアダプターを閉じる。"""
        if self.adapter:
            try:
                self.adapter.close()
            except Exception:
                pass
            finally:
                self.adapter = None
                self.map_ctrl = None

    # ──────────────────────────────────────────
    # チャット操作（アダプター委譲）
    # ──────────────────────────────────────────

    def _get_chat_messages(self) -> list[dict]:
        """チャットメッセージを取得する。"""
        if self.adapter is None:
            return []
        return self.adapter.get_chat_messages()

    def _post_message(self, character_name: str, text: str) -> bool:
        """チャットメッセージを送信する。"""
        if self.adapter is None:
            return False
        return self.adapter.send_chat(character_name, text)

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

        # マップ操作ツール
        if self.map_ctrl:
            result = execute_map_tool(self.map_ctrl, tool_name, tool_args)
            return False, json.dumps(result, ensure_ascii=False, default=str)

        return False, json.dumps({"error": f"未知のツール: {tool_name}"})

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

        sys_prompt = (
            f"{self.world_setting}\n\n{prompt_tmpl['system'] if prompt_tmpl else ''}\n\n"
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
                        raw = self.adapter.take_screenshot()
                        if raw:
                            screenshot_b64 = base64.b64encode(raw).decode("ascii")
                    except Exception:
                        pass

                content, tool_calls = self.lm_client.generate_with_tools(
                    messages,
                    ALL_TOOLS,
                    temperature=0.7,
                    max_tokens=1500,
                    image_base64=screenshot_b64,
                )

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
        time.sleep(2)
        self._known_messages = [f"{m['speaker']}|{m['body']}" for m in self._get_chat_messages()]

        while self._running:
            current = self._get_chat_messages()
            new_msgs = [
                m for m in current if f"{m['speaker']}|{m['body']}" not in self._known_messages
            ]

            if new_msgs:
                for msg in new_msgs:
                    speaker, body = msg["speaker"], msg["body"]
                    self._known_messages.append(f"{speaker}|{body}")
                    self.sm.log_message(speaker, body)
                    self.ctx.add_message(speaker, body)
                    print(f"\n📨 新着: [{speaker}] {body[:40]}")

                    if "＞" in body or any(
                        k in body for k in self.detector.keyword_map.get("meta_gm", [])
                    ):
                        target_char = self.cm.get_character("meta_gm")
                        enriched = (
                            f"{self.ctx.get_context_summary()}\n\n"
                            f"【今回反応すべき発言】\n[{speaker}]: {body}"
                        )

                        if self.ctx.phase in ["combat", "mission"]:
                            self._run_agent_loop(target_char, "meta_gm", enriched)
                        else:
                            prompt_tmpl = self.pm.get_template(target_char.get("prompt_id"))
                            sys_prompt = (
                                f"{self.world_setting}\n\n"
                                f"{prompt_tmpl.get('system', '') if prompt_tmpl else ''}\n"
                                "※重要: 内部の思考プロセス（Thinking Process）は極力短く済ませ、"
                                "プレイヤーへの返答テキストを直ちに出力してください。"
                            )

                            res, _ = self.lm_client.generate_response(
                                system_prompt=sys_prompt,
                                user_message=enriched,
                                max_tokens=4096,
                            )

                            if res:
                                self._post_message(target_char["name"], f"[AI] {res}")
                                self.ctx.add_message(target_char["name"], res, is_ai=True)
                                print(f"   ✓ 応答: {res[:40]}...")

            self._known_messages = self._known_messages[-300:]
            time.sleep(self.poll_interval)

    def _stdin_monitor_loop(self) -> None:
        """ランチャーからの送信命令を受け取る監視ループ。"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "chat":
                    text = data.get("text", "")
                    char_name = data.get("character", "GM")
                    print(f"📥 ランチャーから送信命令を受信: {text[:20]}...")
                    self._post_message(char_name, text)
                elif data.get("type") == "quit":
                    self._running = False
                    break
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"❌ 標準入力の処理エラー: {e}")

    # ──────────────────────────────────────────
    # メインエントリーポイント
    # ──────────────────────────────────────────

    def start(self) -> None:
        print("=" * 50 + "\nタクティカル祓魔師 CCFolia連携\n" + "=" * 50)
        self.sm.start_new_session("CCFoliaSession")
        self._init_adapter()
        self._init_knowledge()
        self._running = True

        threading.Thread(target=self._monitor_loop, daemon=True).start()
        threading.Thread(target=self._stdin_monitor_loop, daemon=True).start()

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self._running = False
            print("終了します")
            self._close_adapter()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--room", required=True)
    parser.add_argument("--default", default="meta_gm")
    args = parser.parse_args()
    CCFoliaConnector(args.room, args.default).start()
