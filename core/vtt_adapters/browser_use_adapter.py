"""browser_use_adapter.py — Browser Use ベースの VTT アダプター。

DOM操作は Browser Use エージェントに委譲し、
Canvas操作（駒移動等）は内部 Playwright ページを直接操作する
ハイブリッド方式の VTT アダプター。
"""

from __future__ import annotations

import logging

try:
    from core.browser_use_agent import AgentTaskResult, BrowserUseAgentWrapper
    from core.vtt_adapters.base_adapter import BaseVTTAdapter
    from core.vtt_adapters.playwright_utils import (
        GRID_SIZE,
        clip_screenshot,
        get_board_state_from_page,
        get_canvas_bounds,
        spawn_piece_clipboard,
    )
except ModuleNotFoundError:
    from browser_use_agent import AgentTaskResult, BrowserUseAgentWrapper  # type: ignore[no-redef]
    from vtt_adapters.base_adapter import BaseVTTAdapter  # type: ignore[no-redef]
    from vtt_adapters.playwright_utils import (  # type: ignore[no-redef]
        GRID_SIZE,
        clip_screenshot,
        get_board_state_from_page,
        get_canvas_bounds,
        spawn_piece_clipboard,
    )

logger = logging.getLogger(__name__)


class BrowserUseVTTAdapter(BaseVTTAdapter):
    """Browser Use ベースの VTT アダプター。

    DOM操作（チャット送信、ルーム作成等）は Browser Use エージェントに委譲し、
    パフォーマンスクリティカルな操作（チャットポーリング、盤面取得、駒移動）は
    内部の Playwright ページを直接操作する。
    """

    def __init__(
        self,
        model_name: str = "",
        api_key: str = "",
        provider: str = "local",
        headless: bool = False,
        lm_studio_url: str = "http://localhost:1234",
    ) -> None:
        self._agent = BrowserUseAgentWrapper(
            model_name=model_name,
            api_key=api_key,
            provider=provider,
            headless=headless,
            lm_studio_url=lm_studio_url,
        )
        self._page: object | None = None
        self._room_url: str = ""

    # ──────────────────────────────────────────
    # 接続 / 切断
    # ──────────────────────────────────────────

    def connect(self, room_url: str, headless: bool = False,
                cdp_url: str | None = None) -> None:
        """Browser Use でブラウザを起動し、VTT ルームに接続する。"""
        if cdp_url:
            logger.warning(
                "BrowserUseVTTAdapter はCDP接続に対応していません。"
                "通常モードで接続します。"
            )
        self._room_url = room_url
        result = self._agent.run_task_sync(
            f"ブラウザで以下のURLにアクセスしてください: {room_url}\n"
            "ページが完全に読み込まれるまで待ってください。",
            url=room_url,
        )
        if not result.success:
            raise ConnectionError(f"VTT 接続失敗: {result.error}")

        # ハイブリッド操作用に Playwright Page を取得
        self._page = self._agent.get_playwright_page_sync()
        logger.info("Browser Use で VTT に接続: %s", room_url)

    def close(self) -> None:
        """ブラウザを閉じて接続を切断する。"""
        self._agent.close_sync()
        self._page = None
        logger.info("Browser Use 接続を切断")

    # ──────────────────────────────────────────
    # DOM操作（Browser Use 委譲）
    # ──────────────────────────────────────────

    def send_chat(self, character_name: str, text: str) -> bool:
        """Browser Use でチャットメッセージを送信する。"""
        task = self._agent.format_task(
            "send_chat", character_name=character_name, text=text,
        )
        result = self._agent.run_task_sync(task)
        if not result.success:
            logger.error("チャット送信失敗: %s", result.error)
        return result.success

    # ──────────────────────────────────────────
    # 直接 Playwright 操作（高速パス）
    # ──────────────────────────────────────────

    @property
    def page(self) -> object:
        """アクティブな Playwright Page を返す。"""
        if self._page is None:
            raise RuntimeError("BrowserUseVTTAdapter: connect() が呼ばれていません")
        return self._page

    def get_chat_messages(self) -> list[dict]:
        """Playwright で直接チャットメッセージを取得する（高速ポーリング用）。"""
        try:
            raw = self.page.evaluate("""() => {  // type: ignore[union-attr]
                const msgs = [];
                const items = document.querySelectorAll(
                    'div.MuiListItemText-root, [class*="message"], [class*="chat-item"]'
                );
                items.forEach(el => {
                    const primary = el.querySelector(
                        '.MuiListItemText-primary, [class*="name"], [class*="speaker"]'
                    );
                    const secondary = el.querySelector(
                        '.MuiListItemText-secondary, [class*="body"], [class*="content"]'
                    );
                    if (primary && secondary) {
                        msgs.push({
                            speaker: primary.textContent.trim(),
                            body: secondary.textContent.trim()
                        });
                    }
                });
                return msgs;
            }""")
            return raw if isinstance(raw, list) else []
        except Exception as e:
            logger.error("チャット取得エラー: %s", e)
            return []

    def get_board_state(self) -> list[dict]:
        """Playwright で直接盤面状態を取得する（高速パス）。"""
        return get_board_state_from_page(self.page)

    def move_piece(self, piece_id: str, grid_x: int, grid_y: int) -> bool:
        """Playwright で直接駒を移動する。"""
        state = self.get_board_state()
        targets = [p for p in state if piece_id in p.get("img_url", "")]
        if not targets:
            logger.warning("駒が見つかりません: %s", piece_id)
            return False

        target = targets[0]
        delta_x = grid_x * GRID_SIZE - target["px_x"]
        delta_y = grid_y * GRID_SIZE - target["px_y"]

        moved = self.page.evaluate(  # type: ignore[union-attr]
            """([index, deltaX, deltaY]) => {
                const els = document.querySelectorAll('.movable');
                const el = els[index];
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;

                const opts = {bubbles: true, cancelable: true, clientX: cx, clientY: cy};
                el.dispatchEvent(new PointerEvent('pointerdown', opts));
                el.dispatchEvent(new MouseEvent('mousedown', opts));

                const moveOpts = {
                    bubbles: true, cancelable: true,
                    clientX: cx + deltaX, clientY: cy + deltaY
                };
                el.dispatchEvent(new PointerEvent('pointermove', moveOpts));
                el.dispatchEvent(new MouseEvent('mousemove', moveOpts));

                const upOpts = {
                    bubbles: true, cancelable: true,
                    clientX: cx + deltaX, clientY: cy + deltaY
                };
                el.dispatchEvent(new PointerEvent('pointerup', upOpts));
                el.dispatchEvent(new MouseEvent('mouseup', upOpts));
                return true;
            }""",
            [target["index"], delta_x, delta_y],
        )
        if moved:
            logger.info("駒移動完了: %s → (%d, %d)", piece_id, grid_x, grid_y)
        return bool(moved)

    def spawn_piece(self, character_json: dict) -> bool:
        """クリップボード経由で駒を配置する。"""
        return spawn_piece_clipboard(self.page, character_json)

    def take_screenshot(self) -> bytes | None:
        """画面のスクリーンショットを取得する。"""
        try:
            return self.page.screenshot()  # type: ignore[union-attr]
        except Exception:
            return None

    # ──────────────────────────────────────────
    # Browser Use 拡張操作（Auto Room Builder 用）
    # ──────────────────────────────────────────

    def create_room(self, room_name: str) -> AgentTaskResult:
        """Browser Use で新規ルームを作成する。"""
        task = self._agent.format_task("create_room", room_name=room_name)
        return self._agent.run_task_sync(task)

    def create_character(self, name: str, params: str) -> AgentTaskResult:
        """Browser Use でキャラクターを作成する。"""
        task = self._agent.format_task(
            "create_character", name=name, params=params,
        )
        return self._agent.run_task_sync(task)

    def set_character_params(
        self, name: str, hp: str, initiative: str
    ) -> AgentTaskResult:
        """Browser Use でキャラクターパラメータを設定する。"""
        task = self._agent.format_task(
            "set_character_params", name=name, hp=hp, initiative=initiative,
        )
        return self._agent.run_task_sync(task)

    def upload_image(self, file_path: str, asset_type: str) -> AgentTaskResult:
        """Browser Use で画像をアップロードする。"""
        task = self._agent.format_task(
            "upload_image", file_path=file_path, asset_type=asset_type,
        )
        return self._agent.run_task_sync(task)

    def switch_bgm(self, bgm_name: str) -> AgentTaskResult:
        """Browser Use でBGMを切り替える。"""
        task = self._agent.format_task("switch_bgm", bgm_name=bgm_name)
        return self._agent.run_task_sync(task)

    def set_background(self, image_url: str) -> AgentTaskResult:
        """Browser Use で背景画像を変更する。"""
        task = self._agent.format_task("set_background", image_url=image_url)
        return self._agent.run_task_sync(task)

    # ──────────────────────────────────────────
    # Phase 2: アセットアップロード / Canvas Vision
    # ──────────────────────────────────────────

    def upload_asset(self, file_path: str, asset_type: str) -> str | None:
        """AssetUploader 経由でファイルをアップロードする。"""
        try:
            from core.asset_uploader import AssetUploader
        except ModuleNotFoundError:
            from asset_uploader import AssetUploader  # type: ignore[no-redef]
        uploader = AssetUploader(page=self.page, agent=self._agent)
        if asset_type == "bgm":
            return uploader.upload_audio(file_path)
        return uploader.upload_image(file_path, asset_type)

    def take_canvas_screenshot(self) -> bytes | None:
        """Canvas / ボード領域のみのスクリーンショットを取得する。"""
        bounds = get_canvas_bounds(self.page)
        if bounds:
            return clip_screenshot(self.page, bounds)
        return self.take_screenshot()

    def get_vision_controller(self) -> object:
        """VisionCanvasController のインスタンスを生成して返す。"""
        try:
            from core.config import load_config
            from core.vision_canvas_controller import VisionCanvasController
        except ModuleNotFoundError:
            from config import load_config  # type: ignore[no-redef]
            from vision_canvas_controller import VisionCanvasController  # type: ignore[no-redef]
        cfg = load_config()
        return VisionCanvasController(
            page=self.page,
            cloud_api_key=cfg.get("openai_api_key", ""),
            cloud_model=cfg.get("vlm_model", ""),
            vlm_provider=cfg.get("vlm_provider", "local"),
        )
