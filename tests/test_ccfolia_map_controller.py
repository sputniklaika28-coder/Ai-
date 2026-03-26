"""
test_ccfolia_map_controller.py — CCFoliaMapController のユニットテスト（リファクタ版）

pyautogui を排除しアダプターに委譲する新設計のテスト。

テスト対象:
  - parse_xy() / extract_hash() ユーティリティ
  - get_board_state() / move_piece() のアダプター委譲
  - execute_map_tool() ディスパッチャー
"""

from unittest.mock import MagicMock

import pytest

from core.ccfolia_map_controller import CCFoliaMapController, execute_map_tool


@pytest.fixture
def mock_adapter():
    """BaseVTTAdapter を模倣する MagicMock"""
    adapter = MagicMock()
    adapter.get_board_state.return_value = [
        {
            "index": 0,
            "img_hash": "abcdef12",
            "img_url": "https://ccfolia.com/files/abcdef1234567890/img.png",
            "px_x": 96,
            "px_y": 192,
            "grid_x": 1,
            "grid_y": 2,
        }
    ]
    adapter.move_piece.return_value = True
    adapter.spawn_piece.return_value = True
    return adapter


@pytest.fixture
def controller(mock_adapter) -> CCFoliaMapController:
    return CCFoliaMapController(adapter=mock_adapter)


@pytest.fixture
def controller_no_adapter() -> CCFoliaMapController:
    return CCFoliaMapController(adapter=None)


# ──────────────────────────────────────────
# parse_xy
# ──────────────────────────────────────────


class TestParseXY:
    def test_normal_translate(self):
        assert CCFoliaMapController.parse_xy("translate(96px, 192px)") == (96, 192)

    def test_negative_values(self):
        assert CCFoliaMapController.parse_xy("translate(-48px, -96px)") == (-48, -96)

    def test_float_values_truncated(self):
        x, y = CCFoliaMapController.parse_xy("translate(96.5px, 192.8px)")
        assert x == 96
        assert y == 192

    def test_invalid_returns_zero(self):
        assert CCFoliaMapController.parse_xy("no-transform") == (0, 0)

    def test_none_returns_zero(self):
        assert CCFoliaMapController.parse_xy(None) == (0, 0)


# ──────────────────────────────────────────
# extract_hash
# ──────────────────────────────────────────


class TestExtractHash:
    def test_extracts_8_chars_from_shared(self):
        url = "https://ccfolia.com/shared/abcdef1234567890/img.png"
        assert CCFoliaMapController.extract_hash(url) == "abcdef12"

    def test_extracts_from_files_path(self):
        url = "https://ccfolia.com/files/deadbeef99999999/piece.png"
        assert CCFoliaMapController.extract_hash(url) == "deadbeef"

    def test_returns_empty_on_no_match(self):
        assert CCFoliaMapController.extract_hash("https://example.com/img.png") == ""

    def test_none_url_returns_empty(self):
        assert CCFoliaMapController.extract_hash(None) == ""


# ──────────────────────────────────────────
# get_board_state (アダプター委譲)
# ──────────────────────────────────────────


class TestGetBoardState:
    def test_delegates_to_adapter(self, controller, mock_adapter):
        state = controller.get_board_state()
        mock_adapter.get_board_state.assert_called_once()
        assert isinstance(state, list)
        assert len(state) > 0

    def test_piece_has_required_keys(self, controller):
        state = controller.get_board_state()
        piece = state[0]
        for key in ("index", "img_hash", "img_url", "px_x", "px_y", "grid_x", "grid_y"):
            assert key in piece, f"キー '{key}' が欠けています"

    def test_returns_empty_without_adapter(self, controller_no_adapter):
        assert controller_no_adapter.get_board_state() == []


# ──────────────────────────────────────────
# move_piece (アダプター委譲)
# ──────────────────────────────────────────


class TestMovePiece:
    def test_delegates_to_adapter(self, controller, mock_adapter):
        result = controller.move_piece("abcdef12", 5, 7)
        mock_adapter.move_piece.assert_called_once_with("abcdef12", 5, 7)
        assert result is True

    def test_returns_false_without_adapter(self, controller_no_adapter):
        assert controller_no_adapter.move_piece("abcdef12", 3, 4) is False


# ──────────────────────────────────────────
# move_piece_by_current_pos
# ──────────────────────────────────────────


class TestMovePieceByCurrentPos:
    def test_finds_piece_and_delegates(self, controller, mock_adapter):
        result = controller.move_piece_by_current_pos(1, 2, 5, 7)
        mock_adapter.move_piece.assert_called_once_with("abcdef12", 5, 7)
        assert result is True

    def test_returns_false_when_no_piece_at_pos(self, controller, mock_adapter):
        result = controller.move_piece_by_current_pos(99, 99, 5, 7)
        mock_adapter.move_piece.assert_not_called()
        assert result is False


# ──────────────────────────────────────────
# spawn_piece
# ──────────────────────────────────────────


class TestSpawnPiece:
    def test_delegates_to_adapter(self, controller, mock_adapter):
        char_data = {"name": "テスト", "hp": 10}
        result = controller.spawn_piece(char_data)
        mock_adapter.spawn_piece.assert_called_once_with(char_data)
        assert result is True

    def test_returns_false_without_adapter(self, controller_no_adapter):
        assert controller_no_adapter.spawn_piece({"name": "テスト"}) is False


# ──────────────────────────────────────────
# execute_map_tool ディスパッチャー
# ──────────────────────────────────────────


class TestExecuteMapTool:
    def test_dispatches_get_board_state(self, controller, mock_adapter):
        execute_map_tool(controller, "get_board_state", {})
        mock_adapter.get_board_state.assert_called_once()

    def test_dispatches_move_piece(self, controller, mock_adapter):
        execute_map_tool(controller, "move_piece", {"img_hash": "abc", "grid_x": 1, "grid_y": 2})
        mock_adapter.move_piece.assert_called_once_with("abc", 1, 2)

    def test_dispatches_spawn_piece(self, controller, mock_adapter):
        data = {"name": "test"}
        execute_map_tool(controller, "spawn_piece", {"character_json": data})
        mock_adapter.spawn_piece.assert_called_once_with(data)

    def test_unknown_tool_returns_error(self, controller):
        result = execute_map_tool(controller, "unknown_tool", {})
        assert "error" in result
