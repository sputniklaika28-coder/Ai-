"""
test_vision_canvas_controller.py — VisionCanvasController のユニットテスト

VLM レスポンスパース・座標変換・グリッドスナップ・
バリデーションをテストする。実際の VLM API は呼び出さない。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.vision_canvas_controller import VisionCanvasController
from core.vtt_adapters.playwright_utils import GRID_SIZE

# ──────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture()
def mock_page():
    page = MagicMock()
    page.evaluate.return_value = {"w": 1280, "h": 900}
    page.screenshot.return_value = b"\x89PNG_FAKE_IMAGE"
    page.mouse = MagicMock()
    return page


@pytest.fixture()
def controller(mock_page):
    return VisionCanvasController(
        page=mock_page,
        cloud_api_key="test-key",
        cloud_model="gpt-4o",
    )


# ──────────────────────────────────────────
# 座標変換
# ──────────────────────────────────────────


class TestCoordinateConversion:
    def test_pixel_to_grid(self):
        gx, gy = VisionCanvasController.pixel_to_grid(192, 288)
        assert gx == 2
        assert gy == 3

    def test_grid_to_pixel(self):
        px, py = VisionCanvasController.grid_to_pixel(2, 3)
        # セル中心: 2*96+48=240, 3*96+48=336
        assert px == 240
        assert py == 336

    def test_pixel_to_grid_rounds(self):
        gx, gy = VisionCanvasController.pixel_to_grid(200, 100)
        assert gx == round(200 / GRID_SIZE)
        assert gy == round(100 / GRID_SIZE)

    def test_grid_to_pixel_zero(self):
        px, py = VisionCanvasController.grid_to_pixel(0, 0)
        assert px == GRID_SIZE // 2
        assert py == GRID_SIZE // 2


# ──────────────────────────────────────────
# グリッドスナップ
# ──────────────────────────────────────────


class TestSnapToGrid:
    def test_snap_exact_grid(self):
        result = VisionCanvasController._snap_to_grid((240, 336))
        # 240/96=2.5 → round=2, 336/96=3.5 → round=4
        # 実際: round(240/96)=round(2.5)=2 → 2*96+48=240
        #        round(336/96)=round(3.5)=4 → 4*96+48=432
        assert result[0] % GRID_SIZE == GRID_SIZE // 2
        assert result[1] % GRID_SIZE == GRID_SIZE // 2

    def test_snap_off_center(self):
        result = VisionCanvasController._snap_to_grid((100, 50))
        # 100/96 ≈ 1.04 → round=1 → 1*96+48=144
        # 50/96  ≈ 0.52 → round=1 → 1*96+48=144
        assert result == (144, 144)

    def test_snap_returns_grid_center(self):
        """スナップ結果は常にグリッドセル中心座標。"""
        for x in range(0, 500, 37):
            for y in range(0, 500, 41):
                sx, sy = VisionCanvasController._snap_to_grid((x, y))
                assert sx % GRID_SIZE == GRID_SIZE // 2
                assert sy % GRID_SIZE == GRID_SIZE // 2


# ──────────────────────────────────────────
# ビューポート検証
# ──────────────────────────────────────────


class TestIsInViewport:
    def test_inside(self):
        assert VisionCanvasController._is_in_viewport((100, 200), (1280, 900))

    def test_origin(self):
        assert VisionCanvasController._is_in_viewport((0, 0), (1280, 900))

    def test_edge(self):
        assert VisionCanvasController._is_in_viewport((1280, 900), (1280, 900))

    def test_outside_x(self):
        assert not VisionCanvasController._is_in_viewport((1281, 500), (1280, 900))

    def test_outside_y(self):
        assert not VisionCanvasController._is_in_viewport((500, 901), (1280, 900))

    def test_negative(self):
        assert not VisionCanvasController._is_in_viewport((-1, 500), (1280, 900))


# ──────────────────────────────────────────
# JSON 抽出
# ──────────────────────────────────────────


class TestExtractJson:
    def test_extract_from_markdown_block(self):
        text = '```json\n{"px_x": 100, "px_y": 200}\n```'
        result = VisionCanvasController._extract_json(text)
        assert result is not None
        data = json.loads(result)
        assert data["px_x"] == 100

    def test_extract_bare_json(self):
        text = 'The piece is at {"px_x": 50, "px_y": 60} on the board.'
        result = VisionCanvasController._extract_json(text)
        assert result is not None
        data = json.loads(result)
        assert data["px_x"] == 50

    def test_no_json_returns_none(self):
        assert VisionCanvasController._extract_json("No JSON here") is None

    def test_extract_nested_json(self):
        text = '{"pieces": [{"description": "knight", "px_x": 300, "px_y": 400}]}'
        result = VisionCanvasController._extract_json(text)
        data = json.loads(result)
        assert "pieces" in data


# ──────────────────────────────────────────
# レスポンスパース（複数座標）
# ──────────────────────────────────────────


class TestParseCoordinates:
    def test_parse_list_in_pieces_key(self, controller):
        response = json.dumps({"pieces": [
            {"description": "warrior", "px_x": 100, "px_y": 200},
            {"description": "mage", "px_x": 300, "px_y": 400},
        ]})
        result = controller._parse_coordinates(response)
        assert len(result) == 2
        assert result[0]["description"] == "warrior"

    def test_parse_pieces_key_format(self, controller):
        response = json.dumps({
            "pieces": [
                {"description": "goblin", "px_x": 500, "px_y": 600},
            ]
        })
        result = controller._parse_coordinates(response)
        assert len(result) == 1
        assert result[0]["description"] == "goblin"

    def test_parse_empty_response(self, controller):
        assert controller._parse_coordinates("") == []

    def test_parse_invalid_json(self, controller):
        assert controller._parse_coordinates("not json at all") == []


# ──────────────────────────────────────────
# レスポンスパース（単一座標）
# ──────────────────────────────────────────


class TestParseSingleCoordinate:
    def test_parse_valid(self, controller):
        response = '{"px_x": 144, "px_y": 240}'
        result = controller._parse_single_coordinate(response)
        assert result == (144, 240)

    def test_parse_from_markdown(self, controller):
        response = '```json\n{"px_x": 48, "px_y": 48}\n```'
        result = controller._parse_single_coordinate(response)
        assert result == (48, 48)

    def test_parse_missing_keys(self, controller):
        response = '{"x": 100, "y": 200}'
        result = controller._parse_single_coordinate(response)
        assert result is None

    def test_parse_no_json(self, controller):
        result = controller._parse_single_coordinate("I don't see any piece")
        assert result is None


# ──────────────────────────────────────────
# 座標バリデーション
# ──────────────────────────────────────────


class TestValidateCoordinates:
    def test_filters_out_of_bounds(self, controller):
        coords = [
            {"description": "ok", "px_x": 100, "px_y": 200},
            {"description": "oob", "px_x": 9999, "px_y": 100},
        ]
        result = controller._validate_coordinates(coords, (1280, 900))
        assert len(result) == 1
        assert result[0]["description"] == "ok"

    def test_snaps_to_grid(self, controller):
        coords = [{"description": "test", "px_x": 100, "px_y": 50}]
        result = controller._validate_coordinates(coords, (1280, 900))
        assert result[0]["px_x"] % GRID_SIZE == GRID_SIZE // 2
        assert result[0]["px_y"] % GRID_SIZE == GRID_SIZE // 2

    def test_adds_grid_coords(self, controller):
        coords = [{"description": "test", "px_x": 240, "px_y": 336}]
        result = controller._validate_coordinates(coords, (1280, 900))
        assert "grid_x" in result[0]
        assert "grid_y" in result[0]

    def test_empty_list(self, controller):
        assert controller._validate_coordinates([], (1280, 900)) == []

    def test_invalid_coord_types_skipped(self, controller):
        coords = [{"description": "bad", "px_x": "abc", "px_y": "def"}]
        result = controller._validate_coordinates(coords, (1280, 900))
        assert len(result) == 0


# ──────────────────────────────────────────
# VLM バックエンド選択
# ──────────────────────────────────────────


class TestCallVlm:
    def test_uses_cloud_when_api_key_present(self, controller):
        with patch.object(controller, "_call_cloud_vlm", return_value="ok") as m:
            result = controller._call_vlm("test", "base64img")
            m.assert_called_once()
            assert result == "ok"

    def test_uses_local_when_provider_local(self, mock_page):
        lm = MagicMock()
        lm.generate_with_tools.return_value = ("local response", None)
        ctrl = VisionCanvasController(
            page=mock_page, vlm_provider="local", lm_client=lm,
        )
        result = ctrl._call_vlm("test", "base64img")
        assert result == "local response"

    def test_falls_back_to_local_when_no_api_key(self, mock_page):
        lm = MagicMock()
        lm.generate_with_tools.return_value = ("fallback", None)
        ctrl = VisionCanvasController(
            page=mock_page, cloud_api_key="", lm_client=lm,
        )
        result = ctrl._call_vlm("test", "base64img")
        assert result == "fallback"

    def test_returns_empty_when_no_backend(self, mock_page):
        ctrl = VisionCanvasController(page=mock_page, cloud_api_key="")
        result = ctrl._call_vlm("test", "base64img")
        assert result == ""


# ──────────────────────────────────────────
# Canvas 操作
# ──────────────────────────────────────────


class TestCanvasOperations:
    def test_click_at(self, controller, mock_page):
        assert controller.click_at(100, 200) is True
        mock_page.mouse.click.assert_called_once_with(100, 200)

    def test_click_at_error(self, controller, mock_page):
        mock_page.mouse.click.side_effect = Exception("click failed")
        assert controller.click_at(100, 200) is False

    def test_drag_piece_calls_mouse_drag(self, controller):
        with patch("core.vision_canvas_controller.mouse_drag", return_value=True) as m:
            result = controller.drag_piece((100, 200), (300, 400))
            assert result is True
            m.assert_called_once()


# ──────────────────────────────────────────
# スクリーンショット
# ──────────────────────────────────────────


class TestScreenshot:
    def test_canvas_screenshot_with_bounds(self, controller, mock_page):
        with patch("core.vision_canvas_controller.get_canvas_bounds") as gb, \
             patch("core.vision_canvas_controller.clip_screenshot") as cs:
            gb.return_value = {"x": 0, "y": 0, "width": 800, "height": 600}
            cs.return_value = b"\x89PNG"
            result = controller.take_canvas_screenshot()
            assert result == b"\x89PNG"

    def test_canvas_screenshot_fallback(self, controller, mock_page):
        with patch("core.vision_canvas_controller.get_canvas_bounds", return_value=None):
            result = controller.take_canvas_screenshot()
            assert result == b"\x89PNG_FAKE_IMAGE"

    def test_b64_screenshot(self, controller):
        with patch.object(controller, "take_canvas_screenshot", return_value=b"test"):
            result = controller.take_canvas_screenshot_b64()
            assert result is not None
            import base64
            assert base64.b64decode(result) == b"test"

    def test_b64_screenshot_none(self, controller):
        with patch.object(controller, "take_canvas_screenshot", return_value=None):
            assert controller.take_canvas_screenshot_b64() is None
