"""
test_vtt_adapters.py — VTTアダプターのユニットテスト

BaseVTTAdapter のインターフェース準拠と、
CCFoliaAdapter の各メソッドをモック環境でテストする。
"""

from unittest.mock import MagicMock

import pytest

from core.vtt_adapters.base_adapter import BaseVTTAdapter
from core.vtt_adapters.ccfolia_adapter import CCFoliaAdapter

# ──────────────────────────────────────────
# BaseVTTAdapter インターフェーステスト
# ──────────────────────────────────────────


class TestBaseVTTAdapterInterface:
    """BaseVTTAdapter は直接インスタンス化できないことを確認"""

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseVTTAdapter()

    def test_concrete_subclass_must_implement_all_methods(self):
        """必要なメソッドが1つでも欠けているとインスタンス化できない"""

        class IncompleteAdapter(BaseVTTAdapter):
            def connect(self, room_url, headless=False):
                pass

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_complete_subclass_can_instantiate(self):
        """全メソッドを実装したサブクラスはインスタンス化できる"""

        class CompleteAdapter(BaseVTTAdapter):
            def connect(self, room_url, headless=False): pass
            def close(self): pass
            def get_board_state(self): return []
            def move_piece(self, piece_id, grid_x, grid_y): return True
            def spawn_piece(self, character_json): return True
            def send_chat(self, character_name, text): return True
            def get_chat_messages(self): return []
            def take_screenshot(self): return None

        adapter = CompleteAdapter()
        assert isinstance(adapter, BaseVTTAdapter)


# ──────────────────────────────────────────
# CCFoliaAdapter ユーティリティテスト
# ──────────────────────────────────────────


class TestCCFoliaAdapterParseXY:
    def test_normal_translate(self):
        assert CCFoliaAdapter._parse_xy("translate(96px, 192px)") == (96, 192)

    def test_negative_values(self):
        assert CCFoliaAdapter._parse_xy("translate(-48px, -96px)") == (-48, -96)

    def test_float_truncation(self):
        assert CCFoliaAdapter._parse_xy("translate(96.5px, 192.8px)") == (96, 192)

    def test_none_returns_zero(self):
        assert CCFoliaAdapter._parse_xy(None) == (0, 0)

    def test_invalid_returns_zero(self):
        assert CCFoliaAdapter._parse_xy("scale(2)") == (0, 0)


class TestCCFoliaAdapterExtractHash:
    def test_shared_url(self):
        assert CCFoliaAdapter._extract_hash(
            "https://ccfolia.com/shared/abcdef1234567890/img.png"
        ) == "abcdef12"

    def test_files_url(self):
        assert CCFoliaAdapter._extract_hash(
            "https://ccfolia.com/files/deadbeef99999999/piece.png"
        ) == "deadbeef"

    def test_no_match(self):
        assert CCFoliaAdapter._extract_hash("https://example.com/img.png") == ""

    def test_none(self):
        assert CCFoliaAdapter._extract_hash(None) == ""


# ──────────────────────────────────────────
# CCFoliaAdapter ブラウザ操作テスト（モック）
# ──────────────────────────────────────────


@pytest.fixture
def mock_page():
    """Playwright Page オブジェクトのモック"""
    page = MagicMock()
    page.evaluate.return_value = [
        {
            "index": 0,
            "transform": "translate(96px, 192px)",
            "imgSrc": "https://ccfolia.com/files/abcdef1234567890/img.png",
            "vx": 150.0,
            "vy": 250.0,
        }
    ]
    page.screenshot.return_value = b"\x89PNG\r\n"
    page.query_selector_all.return_value = []
    return page


@pytest.fixture
def adapter_with_mock_page(mock_page) -> CCFoliaAdapter:
    """Playwright の起動をスキップして Page をモック差し替えした CCFoliaAdapter"""
    adapter = CCFoliaAdapter()
    adapter._page = mock_page
    return adapter


class TestCCFoliaAdapterGetBoardState:
    def test_returns_list_of_pieces(self, adapter_with_mock_page):
        state = adapter_with_mock_page.get_board_state()
        assert isinstance(state, list)
        assert len(state) == 1

    def test_piece_has_required_keys(self, adapter_with_mock_page):
        state = adapter_with_mock_page.get_board_state()
        piece = state[0]
        for key in ("index", "img_hash", "img_url", "px_x", "px_y", "grid_x", "grid_y"):
            assert key in piece

    def test_grid_calculated_from_px(self, adapter_with_mock_page):
        state = adapter_with_mock_page.get_board_state()
        piece = state[0]
        assert piece["grid_x"] == 1  # 96 / 96
        assert piece["grid_y"] == 2  # 192 / 96

    def test_hash_extracted(self, adapter_with_mock_page):
        state = adapter_with_mock_page.get_board_state()
        assert state[0]["img_hash"] == "abcdef12"

    def test_empty_board(self, adapter_with_mock_page, mock_page):
        mock_page.evaluate.return_value = []
        assert adapter_with_mock_page.get_board_state() == []


class TestCCFoliaAdapterMovePiece:
    def test_calls_evaluate_with_deltas(self, adapter_with_mock_page, mock_page):
        # First evaluate call returns board state, second moves piece
        mock_page.evaluate.side_effect = [
            [
                {
                    "index": 0,
                    "transform": "translate(96px, 192px)",
                    "imgSrc": "https://ccfolia.com/files/abcdef1234567890/img.png",
                    "vx": 150.0,
                    "vy": 250.0,
                }
            ],
            True,  # move result
        ]
        result = adapter_with_mock_page.move_piece("abcdef12", 5, 7)
        assert result is True
        assert mock_page.evaluate.call_count == 2

    def test_returns_false_when_piece_not_found(self, adapter_with_mock_page, mock_page):
        mock_page.evaluate.return_value = []
        result = adapter_with_mock_page.move_piece("nonexistent", 3, 4)
        assert result is False


class TestCCFoliaAdapterSpawnPiece:
    def test_writes_clipboard_and_pastes(self, adapter_with_mock_page, mock_page):
        mock_page.query_selector.return_value = MagicMock()
        result = adapter_with_mock_page.spawn_piece({"name": "テスト", "hp": 10})
        assert result is True
        # clipboard write と Ctrl+V が呼ばれたか確認
        mock_page.evaluate.assert_called()
        mock_page.keyboard.press.assert_any_call("Control+v")


class TestCCFoliaAdapterTakeScreenshot:
    def test_returns_bytes(self, adapter_with_mock_page):
        screenshot = adapter_with_mock_page.take_screenshot()
        assert isinstance(screenshot, bytes)

    def test_returns_none_on_error(self, adapter_with_mock_page, mock_page):
        mock_page.screenshot.side_effect = Exception("error")
        assert adapter_with_mock_page.take_screenshot() is None


class TestCCFoliaAdapterPageProperty:
    def test_raises_without_connect(self):
        adapter = CCFoliaAdapter()
        with pytest.raises(RuntimeError, match="connect"):
            _ = adapter.page


class TestCCFoliaAdapterClose:
    def test_close_without_connect(self):
        adapter = CCFoliaAdapter()
        adapter.close()  # should not raise

    def test_close_resets_state(self, adapter_with_mock_page):
        adapter_with_mock_page.close()
        assert adapter_with_mock_page._page is None
        assert adapter_with_mock_page._browser is None
