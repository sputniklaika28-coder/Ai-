"""
test_browser_use_adapter.py — Browser Use VTT アダプターのユニットテスト

BrowserUseVTTAdapter が BaseVTTAdapter のインターフェースを
正しく実装しているかをテストする。
Browser Use ライブラリが未インストールの環境でも動作する。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.vtt_adapters.base_adapter import BaseVTTAdapter

# ──────────────────────────────────────────
# playwright_utils テスト
# ──────────────────────────────────────────


class TestPlaywrightUtils:
    """playwright_utils の共通関数テスト"""

    def test_parse_xy_normal(self):
        from core.vtt_adapters.playwright_utils import parse_xy
        assert parse_xy("translate(96px, 192px)") == (96, 192)

    def test_parse_xy_negative(self):
        from core.vtt_adapters.playwright_utils import parse_xy
        assert parse_xy("translate(-48px, -96px)") == (-48, -96)

    def test_parse_xy_float(self):
        from core.vtt_adapters.playwright_utils import parse_xy
        assert parse_xy("translate(96.5px, 192.8px)") == (96, 192)

    def test_parse_xy_none(self):
        from core.vtt_adapters.playwright_utils import parse_xy
        assert parse_xy(None) == (0, 0)

    def test_extract_hash_shared(self):
        from core.vtt_adapters.playwright_utils import extract_hash
        assert extract_hash(
            "https://ccfolia.com/shared/abcdef1234567890/img.png"
        ) == "abcdef12"

    def test_extract_hash_files(self):
        from core.vtt_adapters.playwright_utils import extract_hash
        assert extract_hash(
            "https://ccfolia.com/files/deadbeef99999999/piece.png"
        ) == "deadbeef"

    def test_extract_hash_none(self):
        from core.vtt_adapters.playwright_utils import extract_hash
        assert extract_hash(None) == ""

    def test_get_board_state_from_page(self):
        from core.vtt_adapters.playwright_utils import get_board_state_from_page

        mock_page = MagicMock()
        mock_page.evaluate.return_value = [
            {
                "index": 0,
                "transform": "translate(96px, 192px)",
                "imgSrc": "https://ccfolia.com/files/abcdef1234567890/img.png",
                "vx": 150.0,
                "vy": 250.0,
            }
        ]
        result = get_board_state_from_page(mock_page)
        assert len(result) == 1
        assert result[0]["grid_x"] == 1
        assert result[0]["grid_y"] == 2
        assert result[0]["img_hash"] == "abcdef12"

    def test_get_board_state_empty(self):
        from core.vtt_adapters.playwright_utils import get_board_state_from_page

        mock_page = MagicMock()
        mock_page.evaluate.return_value = []
        assert get_board_state_from_page(mock_page) == []

    def test_spawn_piece_clipboard(self):
        from core.vtt_adapters.playwright_utils import spawn_piece_clipboard

        mock_page = MagicMock()
        mock_page.query_selector.return_value = MagicMock()
        result = spawn_piece_clipboard(mock_page, {"name": "テスト", "hp": 10})
        assert result is True
        mock_page.keyboard.press.assert_any_call("Control+v")


# ──────────────────────────────────────────
# BrowserUseVTTAdapter インターフェーステスト
# ──────────────────────────────────────────


class TestBrowserUseVTTAdapterInterface:
    """BrowserUseVTTAdapter が BaseVTTAdapter を正しく実装しているか確認"""

    def test_import_succeeds(self):
        """モジュールのインポート自体は browser-use なしでも成功する"""
        # browser_use_agent 内で _HAS_BROWSER_USE=False になるだけ
        import core.vtt_adapters.browser_use_adapter  # noqa: F401

    def test_is_subclass_of_base(self):
        from core.vtt_adapters.browser_use_adapter import BrowserUseVTTAdapter
        assert issubclass(BrowserUseVTTAdapter, BaseVTTAdapter)

    def test_has_all_required_methods(self):
        from core.vtt_adapters.browser_use_adapter import BrowserUseVTTAdapter

        required = [
            "connect", "close", "get_board_state", "move_piece",
            "spawn_piece", "send_chat", "get_chat_messages", "take_screenshot",
        ]
        for method_name in required:
            assert hasattr(BrowserUseVTTAdapter, method_name), (
                f"BrowserUseVTTAdapter に {method_name} がありません"
            )

    def test_has_extended_methods(self):
        """Auto Room Builder 用の拡張メソッドが存在するか"""
        from core.vtt_adapters.browser_use_adapter import BrowserUseVTTAdapter

        extended = [
            "create_room", "create_character", "set_character_params",
            "upload_image", "switch_bgm", "set_background",
        ]
        for method_name in extended:
            assert hasattr(BrowserUseVTTAdapter, method_name), (
                f"BrowserUseVTTAdapter に拡張メソッド {method_name} がありません"
            )
