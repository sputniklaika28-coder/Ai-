"""screen.py — mss + Pillow で OS レベルスクリーンショットを取得する。

`mss` は Linux/Windows/macOS で動作する超高速なキャプチャライブラリ。
取得したピクセルバッファを Pillow で PNG にエンコードする。

対象ウィンドウの bbox は `window_focus.get_bbox()` から渡される想定で、
None の場合は全画面（プライマリモニタ）をキャプチャする。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapturedImage:
    """スクリーンショットの生データと bbox。"""

    png_bytes: bytes
    bbox: tuple[int, int, int, int]  # (left, top, right, bottom)
    viewport: tuple[int, int]        # (width, height)


class ScreenCaptureError(RuntimeError):
    """スクリーンショット取得失敗時の例外。"""


def _import_pil() -> Any:
    try:
        from PIL import Image  # type: ignore[import-not-found]
        return Image
    except ImportError as e:
        raise ScreenCaptureError(
            "Pillow が未導入です。pip install -e .[vlm-agent] を実行してください。"
        ) from e


def _import_mss() -> Any:
    try:
        import mss  # type: ignore[import-not-found]
        return mss
    except ImportError as e:
        raise ScreenCaptureError(
            "mss が未導入です。pip install -e .[vlm-agent] を実行してください。"
        ) from e


def _import_deps() -> tuple[Any, Any]:
    """mss と PIL.Image を遅延 import する（未導入時の明確なエラー用）。"""
    return _import_mss(), _import_pil()


def capture(
    bbox: tuple[int, int, int, int] | None = None,
    monitor_index: int = 1,
) -> CapturedImage:
    """スクリーンショットを取得して PNG バイト列として返す。

    Args:
        bbox: (left, top, right, bottom) の絶対座標。None ならプライマリモニタ全体。
        monitor_index: `mss` の monitors[index]。既定 1 = プライマリモニタ
            （monitors[0] は全モニタの合成矩形）。

    Returns:
        CapturedImage。座標は bbox の左上原点ではなく **モニタ内絶対座標** のまま保持。

    Raises:
        ScreenCaptureError: mss/PIL が未導入、または取得に失敗した場合。
    """
    mss, Image = _import_deps()

    try:
        with mss.mss() as sct:
            if bbox is None:
                monitors = sct.monitors
                if monitor_index < 0 or monitor_index >= len(monitors):
                    monitor_index = 1 if len(monitors) > 1 else 0
                mon = monitors[monitor_index]
                region = {
                    "left": mon["left"],
                    "top": mon["top"],
                    "width": mon["width"],
                    "height": mon["height"],
                }
            else:
                left, top, right, bottom = bbox
                width = max(1, right - left)
                height = max(1, bottom - top)
                region = {"left": left, "top": top, "width": width, "height": height}

            raw = sct.grab(region)
            # mss の bgra バッファから Pillow の RGB 画像へ
            img = Image.frombytes("RGB", raw.size, raw.rgb)
    except ScreenCaptureError:
        raise
    except Exception as e:
        raise ScreenCaptureError(f"スクリーンショット取得失敗: {e}") from e

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    w, h = img.size
    out_bbox = (
        int(region["left"]),
        int(region["top"]),
        int(region["left"] + region["width"]),
        int(region["top"] + region["height"]),
    )
    return CapturedImage(png_bytes=png_bytes, bbox=out_bbox, viewport=(w, h))


def crop_png(png_bytes: bytes, rel_bbox: tuple[int, int, int, int]) -> bytes:
    """既存 PNG を相対座標でクロップして返す（再エンコード）。

    `rel_bbox` は PNG 画像内の (left, top, right, bottom)。
    """
    Image = _import_pil()
    img = Image.open(io.BytesIO(png_bytes))
    cropped = img.crop(rel_bbox)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()
