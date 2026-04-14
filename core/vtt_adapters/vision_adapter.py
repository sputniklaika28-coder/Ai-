"""vision_adapter.py — VLM + pyautogui ビジョン制御 VTT アダプター。

Playwright・API 不要。デスクトップ上で起動中の任意の VTT アプリケーションを、
OS レベルのスクリーンショット + VLM による座標認識 + pyautogui 入力で制御する。

依存:
    pip install -e .[vlm-agent]  (mss, pyautogui, pyperclip, Pillow)
    LMClient（core.lm_client）にマルチモーダルモデルがロードされていること。

再利用コンポーネント:
    addons/vlm_os_agent/screen.py    → スクリーンショット取得
    addons/vlm_os_agent/actuator.py  → マウス・キーボード操作
    addons/vlm_os_agent/window_focus.py → ウィンドウフォーカス
    addons/vlm_os_agent/kill_switch.py → 協調停止
    core/vision_utils.py             → encode_png_b64, extract_json
    core/schemas.py                  → 構造化出力スキーマ
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from core.vision_utils import encode_png_b64, extract_json

try:
    from core.vtt_adapters.base_adapter import BaseVTTAdapter
    from core.schemas import BoardAnalysisResult, ChatLogResult, PieceLocation, SingleCoordinate
except ModuleNotFoundError:
    from vtt_adapters.base_adapter import BaseVTTAdapter  # type: ignore
    from schemas import BoardAnalysisResult, ChatLogResult, PieceLocation, SingleCoordinate  # type: ignore

if TYPE_CHECKING:
    from core.lm_client import LMClient

logger = logging.getLogger(__name__)

# VLM に渡すシステムプロンプト（GUI 要素認識用）
_VISION_SYSTEM_PROMPT = (
    "あなたはデスクトップ GUI の視覚認識エージェントです。"
    "スクリーンショットを解析し、指定された要素のピクセル座標を特定してください。"
    "座標はスクリーンショット画像の左上を (0, 0) とするピクセル座標で回答してください。"
    "必ず指定されたJSONスキーマに従って回答してください。"
)

_BOARD_ANALYSIS_SYSTEM_PROMPT = (
    "あなたはTRPGボードゲームの視覚認識エージェントです。"
    "ゲームボードのスクリーンショットを解析し、"
    "すべてのキャラクタートークン・駒の位置と説明を特定してください。"
    "座標はスクリーンショット内のピクセル座標（左上が 0,0）で回答してください。"
)

_CHAT_EXTRACTION_SYSTEM_PROMPT = (
    "あなたはチャットログの OCR エージェントです。"
    "スクリーンショット内のチャットメッセージをすべて抽出してください。"
    "各メッセージの発言者名と本文を正確に読み取ってください。"
)


class VisionVTTAdapter(BaseVTTAdapter):
    """VLM + pyautogui による汎用ビジョン制御 VTT アダプター。

    任意の VTT アプリケーションをデスクトップ視覚操作で制御する。
    LMClient に画像入力対応のマルチモーダルモデルが必要。

    すべての pyautogui 操作は asyncio executor 経由で実行し、
    イベントループをブロックしない。

    使用例::
        adapter = VisionVTTAdapter(
            lm_client=lm_client,
            window_title="FoundryVTT",
            grid_size=100,
        )
        await adapter.connect(room_url="")
        screenshot = await adapter.take_screenshot()
    """

    def __init__(
        self,
        lm_client: "LMClient",
        window_title: str = "",
        grid_size: int = 100,
        chat_region: tuple[int, int, int, int] | None = None,
        board_region: tuple[int, int, int, int] | None = None,
        max_vlm_steps: int = 6,
        failsafe: bool = True,
    ) -> None:
        """
        Args:
            lm_client: マルチモーダル対応の LMClient インスタンス。
            window_title: 対象ウィンドウタイトルの正規表現。空欄でプライマリモニタ全体。
            grid_size: 1グリッドセルのピクセルサイズ。
            chat_region: チャットログのキャプチャ領域 (left, top, right, bottom)。省略可。
            board_region: ボードのキャプチャ領域 (left, top, right, bottom)。省略可。
            max_vlm_steps: VLM 操作の最大ステップ数。
            failsafe: pyautogui の FAILSAFE（画面四隅で緊急停止）を有効化するか。
        """
        self._lm = lm_client
        self._window_title = window_title
        self._grid_size = grid_size
        self._chat_region = chat_region
        self._board_region = board_region
        self._max_vlm_steps = max_vlm_steps
        self._failsafe = failsafe

        # 遅延初期化（vlm_os_agent 依存）
        self._window_info: Any | None = None
        self._actuator: Any | None = None
        self._kill_switch: Any | None = None
        self._actuator_lock: asyncio.Lock = asyncio.Lock()
        self._connected: bool = False

    # ──────────────────────────────────────
    # 遅延初期化
    # ──────────────────────────────────────

    def _ensure_actuator(self) -> None:
        """Actuator と KillSwitch を遅延初期化する（vlm-agent 依存）。"""
        if self._actuator is not None:
            return
        try:
            from addons.vlm_os_agent.kill_switch import KillSwitch
            from addons.vlm_os_agent.actuator import Actuator
        except ImportError as e:
            raise ImportError(
                "VisionVTTAdapter の使用には vlm-agent extras が必要です。\n"
                "pip install -e .[vlm-agent] を実行してください。"
            ) from e
        self._kill_switch = KillSwitch()
        self._actuator = Actuator(kill_switch=self._kill_switch, failsafe=self._failsafe)

    # ──────────────────────────────────────
    # スクリーンショット
    # ──────────────────────────────────────

    async def _capture(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[bytes, tuple[int, int, int, int]] | None:
        """スクリーンショットを取得する（executor 経由でブロッキング回避）。

        Returns:
            (png_bytes, bbox) または None。
        """
        try:
            from addons.vlm_os_agent.screen import capture, ScreenCaptureError
        except ImportError:
            logger.error("mss/Pillow が未導入です。pip install -e .[vlm-agent] を実行してください。")
            return None

        loop = asyncio.get_event_loop()
        try:
            captured = await loop.run_in_executor(None, capture, region)
            return captured.png_bytes, captured.bbox
        except Exception as e:
            logger.error("スクリーンショット取得失敗: %s", e)
            return None

    async def _capture_b64(
        self,
        region: tuple[int, int, int, int] | None = None,
    ) -> str | None:
        """スクリーンショットを Base64 エンコードして返す。"""
        result = await self._capture(region)
        if result is None:
            return None
        png_bytes, _ = result
        return encode_png_b64(png_bytes)

    # ──────────────────────────────────────
    # VLM 推論
    # ──────────────────────────────────────

    async def _ask_vlm(
        self,
        system_prompt: str,
        user_prompt: str,
        image_b64: str,
        schema_type: type,
    ) -> Any | None:
        """VLM に画像付きプロンプトを送り、Pydantic モデルとしてパースして返す。

        generate_with_tools（マルチモーダル対応）を使い、
        レスポンスを schema_type.model_validate_json() でパースする。
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            content, _ = await self._lm.generate_with_tools(
                messages=messages,
                tools=[],
                temperature=0.1,
                max_tokens=1000,
                image_base64=image_b64,
            )
        except Exception as e:
            logger.error("VLM 推論エラー: %s", e)
            return None

        if not content:
            return None

        json_str = extract_json(content)
        if not json_str:
            logger.warning("VLM レスポンスから JSON を抽出できませんでした: %s", content[:200])
            return None

        try:
            return schema_type.model_validate_json(json_str)
        except Exception as e:
            logger.warning("VLM 構造化パース失敗 (%s): %s", schema_type.__name__, e)
            return None

    async def _find_element(
        self,
        description: str,
        image_b64: str,
    ) -> tuple[int, int] | None:
        """VLM で UI 要素を特定し、ピクセル座標を返す。

        Args:
            description: 探す要素の説明（日本語可）。
            image_b64: スクリーンショットの Base64 文字列。

        Returns:
            (px_x, px_y) または None。
        """
        result: SingleCoordinate | None = await self._ask_vlm(
            system_prompt=_VISION_SYSTEM_PROMPT,
            user_prompt=(
                f"次の UI 要素を見つけてください: {description}\n"
                "見つかった場合は found=true と座標を、見つからない場合は found=false を返してください。"
            ),
            image_b64=image_b64,
            schema_type=SingleCoordinate,
        )
        if result is None or not result.found:
            return None
        return (result.px_x, result.px_y)

    # ──────────────────────────────────────
    # 座標オフセット変換
    # ──────────────────────────────────────

    def _to_absolute(
        self,
        rel_x: int,
        rel_y: int,
        region: tuple[int, int, int, int] | None,
    ) -> tuple[int, int]:
        """クロップ領域からの相対座標を絶対スクリーン座標に変換する。"""
        if region is None:
            return rel_x, rel_y
        return region[0] + rel_x, region[1] + rel_y

    # ──────────────────────────────────────
    # pyautogui ラッパー（executor 経由）
    # ──────────────────────────────────────

    async def _click(self, x: int, y: int) -> None:
        """指定ピクセル座標をクリックする。"""
        self._ensure_actuator()
        loop = asyncio.get_event_loop()
        async with self._actuator_lock:
            await loop.run_in_executor(None, self._actuator.click, x, y)

    async def _drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """指定座標間をドラッグする。"""
        self._ensure_actuator()
        loop = asyncio.get_event_loop()
        async with self._actuator_lock:
            await loop.run_in_executor(
                None,
                lambda: self._actuator.drag(x1, y1, x2, y2),
            )

    async def _type_text(self, text: str) -> None:
        """クリップボード経由でテキストを貼り付ける（CJK 安全）。"""
        self._ensure_actuator()
        loop = asyncio.get_event_loop()
        async with self._actuator_lock:
            await loop.run_in_executor(
                None,
                lambda: self._actuator.type_clipboard(text, submit=False),
            )

    async def _press_enter(self) -> None:
        """Enter キーを押す。"""
        self._ensure_actuator()
        loop = asyncio.get_event_loop()
        async with self._actuator_lock:
            await loop.run_in_executor(None, self._actuator.press, "enter")

    async def _focus_window(self) -> None:
        """対象ウィンドウをフォアグラウンドに移動する。"""
        if self._window_info is None:
            return
        try:
            from addons.vlm_os_agent.window_focus import focus
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, focus, self._window_info)
        except Exception as e:
            logger.warning("ウィンドウフォーカス失敗: %s", e)

    # ──────────────────────────────────────
    # BaseVTTAdapter 実装
    # ──────────────────────────────────────

    async def connect(
        self,
        room_url: str,
        headless: bool = False,
        cdp_url: str | None = None,
    ) -> None:
        """対象ウィンドウを検索してフォーカスする。

        Args:
            room_url: 未使用（ビジョン制御のため無視）。
            headless: 未使用。
            cdp_url: 未使用。

        Note:
            ウィンドウが見つからない場合も例外を送出しない（後で開かれる可能性があるため）。
            window_title が空の場合はプライマリモニタ全体を対象とする。
        """
        self._ensure_actuator()

        if self._window_title:
            try:
                from addons.vlm_os_agent.window_focus import find_window
                loop = asyncio.get_event_loop()
                self._window_info = await loop.run_in_executor(
                    None,
                    lambda: find_window(self._window_title),
                )
                if self._window_info:
                    await self._focus_window()
                    logger.info(
                        "VisionVTTAdapter: ウィンドウ検出 '%s'", self._window_info
                    )
                else:
                    logger.warning(
                        "VisionVTTAdapter: ウィンドウ '%s' が見つかりません（後で再検索）",
                        self._window_title,
                    )
            except Exception as e:
                logger.warning("ウィンドウ検索エラー: %s", e)
        else:
            logger.info("VisionVTTAdapter: window_title 未設定 → プライマリモニタ全体を使用")

        self._connected = True

    async def close(self) -> None:
        """KillSwitch を設定してアクチュエーターを停止する。"""
        if self._kill_switch:
            self._kill_switch.set()
        self._window_info = None
        self._connected = False
        logger.info("VisionVTTAdapter: クローズ")

    async def get_board_state(self) -> list[dict]:
        """VLM でボードを解析し、全駒の位置情報を返す。"""
        image_b64 = await self._capture_b64(self._board_region)
        if image_b64 is None:
            return []

        result: BoardAnalysisResult | None = await self._ask_vlm(
            system_prompt=_BOARD_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=(
                "このゲームボードのスクリーンショット内に存在する"
                "すべてのキャラクタートークン・駒を列挙してください。"
                "各トークンの中心座標と説明を返してください。"
            ),
            image_b64=image_b64,
            schema_type=BoardAnalysisResult,
        )

        if result is None:
            return []

        pieces = []
        for p in result.pieces:
            abs_x, abs_y = self._to_absolute(p.px_x, p.px_y, self._board_region)
            pieces.append({
                "piece_id": p.description,
                "name": p.description,
                "img_url": "",
                "img_hash": "",
                "px_x": abs_x,
                "px_y": abs_y,
                "grid_x": abs_x // self._grid_size,
                "grid_y": abs_y // self._grid_size,
                "description": p.description,
                "confidence": p.confidence,
            })

        return pieces

    async def move_piece(self, piece_id: str, grid_x: int, grid_y: int) -> bool:
        """VLM でトークンを特定し、指定グリッド座標へドラッグする。

        Args:
            piece_id: `get_board_state()` が返した description 文字列。
            grid_x: 移動先グリッド X 座標。
            grid_y: 移動先グリッド Y 座標。
        """
        image_b64 = await self._capture_b64(self._board_region)
        if image_b64 is None:
            return False

        # 駒の現在位置を特定
        src = await self._find_element(
            f"キャラクタートークンまたは駒: {piece_id}", image_b64
        )
        if src is None:
            logger.warning("move_piece: '%s' が見つかりません", piece_id)
            return False

        abs_src_x, abs_src_y = self._to_absolute(src[0], src[1], self._board_region)

        # 移動先のピクセル座標を計算
        board_origin_x = self._board_region[0] if self._board_region else 0
        board_origin_y = self._board_region[1] if self._board_region else 0
        dst_x = board_origin_x + grid_x * self._grid_size + self._grid_size // 2
        dst_y = board_origin_y + grid_y * self._grid_size + self._grid_size // 2

        await self._drag(abs_src_x, abs_src_y, dst_x, dst_y)
        logger.info(
            "move_piece: '%s' → grid(%d, %d) [drag %d,%d → %d,%d]",
            piece_id, grid_x, grid_y, abs_src_x, abs_src_y, dst_x, dst_y,
        )
        return True

    async def spawn_piece(self, character_json: dict) -> bool:
        """キャラクターをボードに配置する。

        Note:
            ビジョン制御の汎用実装ではVTT固有の配置UIが不明なため、
            デフォルトは no-op。VTT固有のサブクラスで override してください。
            例: FoundryVTT ではサイドバーからのドラッグ、
                CCFolia ではクリップボードペーストなど。
        """
        name = character_json.get("name", "unknown")
        logger.warning(
            "spawn_piece: VisionVTTAdapter の汎用実装は配置操作をサポートしません。"
            " キャラクター '%s' の配置をスキップします。"
            " VTT固有のサブクラスで override してください。",
            name,
        )
        return False

    async def send_chat(self, character_name: str, text: str) -> bool:
        """VLM でチャット入力欄を特定し、テキストを送信する。

        Args:
            character_name: 発言者名（テキストに付与）。
            text: 送信テキスト。
        """
        image_b64 = await self._capture_b64()
        if image_b64 is None:
            return False

        coords = await self._find_element(
            "チャット入力欄またはメッセージ入力ボックス", image_b64
        )
        if coords is None:
            logger.warning("send_chat: チャット入力欄が見つかりません")
            return False

        await self._click(coords[0], coords[1])
        await asyncio.sleep(0.2)

        send_text = f"{character_name}: {text}"
        await self._type_text(send_text)
        await asyncio.sleep(0.1)
        await self._press_enter()

        logger.info("send_chat: [%s] %s", character_name, text[:50])
        return True

    async def get_chat_messages(self) -> list[dict]:
        """VLM でチャットログを読み取り、メッセージリストを返す。"""
        image_b64 = await self._capture_b64(self._chat_region)
        if image_b64 is None:
            return []

        result: ChatLogResult | None = await self._ask_vlm(
            system_prompt=_CHAT_EXTRACTION_SYSTEM_PROMPT,
            user_prompt=(
                "このスクリーンショットに表示されているチャットメッセージを"
                "すべて抽出してください。発言者名と本文を読み取ってください。"
            ),
            image_b64=image_b64,
            schema_type=ChatLogResult,
        )

        if result is None:
            return []

        return [
            {"speaker": m.speaker, "body": m.body}
            for m in result.messages
            if m.body.strip()
        ]

    async def take_screenshot(self) -> bytes | None:
        """対象ウィンドウ（またはプライマリモニタ）のスクリーンショットを返す。"""
        result = await self._capture()
        if result is None:
            return None
        png_bytes, _ = result
        return png_bytes
