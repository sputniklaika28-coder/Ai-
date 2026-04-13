"""screen.py のユニットテスト（mss / PIL を mock）。"""

from __future__ import annotations

import io
import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_mss(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """mss モジュールを mock 化して sys.modules に差し込む。"""
    fake_raw = types.SimpleNamespace(
        size=(800, 600),
        rgb=b"\x00" * (800 * 600 * 3),
    )
    fake_sct = MagicMock()
    fake_sct.monitors = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},  # 全体
        {"left": 0, "top": 0, "width": 800, "height": 600},     # プライマリ
    ]
    fake_sct.grab = MagicMock(return_value=fake_raw)

    class FakeMss:
        def __enter__(self):
            return fake_sct

        def __exit__(self, *a):
            return False

    fake_module = types.ModuleType("mss")
    fake_module.mss = MagicMock(return_value=FakeMss())  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mss", fake_module)
    return fake_sct


@pytest.fixture
def ensure_pil() -> None:
    pytest.importorskip("PIL.Image")


class TestCapture:
    def test_full_screen(self, mock_mss, ensure_pil) -> None:
        from addons.vlm_os_agent.screen import capture

        img = capture()
        assert img.viewport == (800, 600)
        assert img.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"  # PNG マジック
        assert img.bbox == (0, 0, 800, 600)

    def test_bbox_crop(self, mock_mss, ensure_pil) -> None:
        from addons.vlm_os_agent.screen import capture

        # mock_mss は grab() の返却を (800,600) にハードコードしているので
        # bbox 指定時も PIL が返す画像サイズはその値になる
        img = capture(bbox=(10, 20, 110, 220))
        assert img.bbox == (10, 20, 110, 220)

    def test_missing_mss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from addons.vlm_os_agent.screen import ScreenCaptureError, capture

        monkeypatch.setitem(sys.modules, "mss", None)
        with pytest.raises(ScreenCaptureError, match="mss"):
            capture()

    def test_grab_failure(self, mock_mss, ensure_pil) -> None:
        from addons.vlm_os_agent.screen import ScreenCaptureError, capture

        mock_mss.grab.side_effect = RuntimeError("display error")
        with pytest.raises(ScreenCaptureError, match="スクリーンショット取得失敗"):
            capture()


class TestCropPng:
    def test_roundtrip(self, ensure_pil) -> None:
        from addons.vlm_os_agent.screen import crop_png
        from PIL import Image

        img = Image.new("RGB", (100, 100), (255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        src = buf.getvalue()

        cropped = crop_png(src, (10, 10, 50, 50))
        out = Image.open(io.BytesIO(cropped))
        assert out.size == (40, 40)
