"""perceive.py — Set-of-Mark（SoM）番号札オーバーレイ。

VLM に生スクショを投げて座標を返させると精度がブレる（Phase 1 の素通しモード）。
Phase 3 では OpenCV でクリック候補矩形を検出し、番号札を重ねた画像を生成し、
VLM には「何番？」とだけ聞く経路を提供する（ID → ピクセル座標変換はローカル）。

バックエンド:
  - "none": 素通し（Phase 1）
  - "cv":   OpenCV ベースの簡易 SoM（Phase 3、[vlm-agent-som] extras が必要）
  - "omniparser": 将来の拡張ポイント（未実装、"cv" へフォールバック）
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mark:
    """番号札 1 つ分の情報。"""

    id: int
    bbox: tuple[int, int, int, int]  # (left, top, right, bottom) 画像内相対座標
    center: tuple[int, int]


@dataclass(frozen=True)
class PerceiveResult:
    """SoM 結果。`annotated_png` は番号札を重ねた PNG。素通し時は元画像を返す。"""

    annotated_png: bytes
    marks: list[Mark]


def annotate(
    png_bytes: bytes,
    *,
    backend: str = "none",
    max_marks: int = 50,
) -> PerceiveResult:
    """スクショに SoM 番号札を重ねる。backend="none" なら素通し。"""
    backend = (backend or "none").lower()
    if backend == "none":
        return PerceiveResult(annotated_png=png_bytes, marks=[])
    if backend in {"cv", "omniparser"}:
        try:
            return _annotate_cv(png_bytes, max_marks=max_marks)
        except PerceiveUnavailable as e:
            logger.warning("SoM バックエンド不可: %s（素通しへフォールバック）", e)
            return PerceiveResult(annotated_png=png_bytes, marks=[])
    logger.warning("未知の perceive backend '%s'（素通しへフォールバック）", backend)
    return PerceiveResult(annotated_png=png_bytes, marks=[])


def find_mark(marks: list[Mark], mark_id: int) -> Mark | None:
    """ID からマークを逆引き。"""
    for m in marks:
        if m.id == mark_id:
            return m
    return None


# ──────────────────────────────────────
# CV ベース実装
# ──────────────────────────────────────


class PerceiveUnavailable(RuntimeError):
    """OpenCV などのオプション依存が未導入。"""


def _import_cv() -> tuple[Any, Any, Any]:
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-not-found]
    except ImportError as e:
        raise PerceiveUnavailable(
            "cv backend には opencv-python / numpy / Pillow が必要です。"
            "pip install -e .[vlm-agent-som] を実行してください。"
        ) from e
    return cv2, np, (Image, ImageDraw, ImageFont)


def _annotate_cv(png_bytes: bytes, *, max_marks: int = 50) -> PerceiveResult:
    """OpenCV の Canny + findContours で候補矩形を検出し、番号札を重ねる。"""
    cv2, np, pil = _import_cv()
    Image, ImageDraw, _ImageFont = pil

    # PIL で読み込み → numpy BGR へ
    img_pil = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img_np = np.array(img_pil)[:, :, ::-1].copy()  # RGB→BGR
    gray = cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 50, 150)
    # 膨張で近いエッジを連結
    kernel = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = gray.shape
    min_area = max(400, (w * h) // 5000)  # 画像サイズ比例
    max_area = (w * h) // 2

    rects: list[tuple[int, int, int, int]] = []
    for cnt in contours:
        x, y, ww, hh = cv2.boundingRect(cnt)
        area = ww * hh
        if area < min_area or area > max_area:
            continue
        # 極端に細長い線は除外
        if ww < 8 or hh < 8:
            continue
        rects.append((x, y, x + ww, y + hh))

    # 面積降順で上位を採用
    rects.sort(key=lambda r: -((r[2] - r[0]) * (r[3] - r[1])))
    rects = rects[:max_marks]

    # PIL で番号札を描画
    annotated = img_pil.copy()
    draw = ImageDraw.Draw(annotated)
    marks: list[Mark] = []
    for i, (l, t, r, b) in enumerate(rects, start=1):
        draw.rectangle([(l, t), (r, b)], outline=(255, 0, 0), width=2)
        label = str(i)
        # 左上に黄色い背景の番号札
        tag_w = 18 + 8 * len(label)
        tag_h = 22
        tag_box = (l, t, l + tag_w, t + tag_h)
        draw.rectangle(tag_box, fill=(255, 220, 0))
        draw.text((l + 4, t + 3), label, fill=(0, 0, 0))
        cx = (l + r) // 2
        cy = (t + b) // 2
        marks.append(Mark(id=i, bbox=(l, t, r, b), center=(cx, cy)))

    buf = io.BytesIO()
    annotated.save(buf, format="PNG")
    return PerceiveResult(annotated_png=buf.getvalue(), marks=marks)
