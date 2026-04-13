"""vision_utils.py — VLM 応答パース・座標検証・pHash などの共有ユーティリティ。

Playwright には依存しない純粋関数群。
`core/vision_canvas_controller.py`（Playwright 版）と `addons/vlm_os_agent/`
（OS 版）の両方から再利用される。
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────
# プロンプトテンプレート
# ──────────────────────────────────────────


def load_vlm_templates(configs_dir: Path) -> dict[str, str]:
    """`configs_dir` 配下の `browser_use_tasks.json` から VLM プロンプトテンプレートを読み込む。

    ファイルが存在しない／不正 JSON の場合は空辞書を返す（呼び出し側のフォールバック用）。
    """
    path = Path(configs_dir) / "browser_use_tasks.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, str)}
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


# ──────────────────────────────────────────
# JSON 抽出・座標パース
# ──────────────────────────────────────────


def extract_json(text: str) -> str | None:
    """テキストから JSON 部分を抽出する。

    1) ```json ... ``` マークダウンブロック
    2) 最初の { から最後の } までの部分
    """
    if not text:
        return None
    # ```json ... ``` ブロック
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # 裸の { ... }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        return text[first : last + 1]
    return None


def parse_coordinates(response: str) -> list[dict]:
    """VLM レスポンスから複数の座標候補を抽出する。

    {"pieces": [...]} または [...] 形式を受け入れる。パースできなければ空リスト。
    """
    try:
        json_str = extract_json(response)
        if not json_str:
            return []
        data = json.loads(json_str)
        if isinstance(data, dict) and "pieces" in data:
            pieces = data["pieces"]
            return pieces if isinstance(pieces, list) else []
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def parse_single_coordinate(response: str) -> tuple[int, int] | None:
    """VLM レスポンスから単一の座標 `(px_x, px_y)` を抽出する。"""
    try:
        json_str = extract_json(response)
        if not json_str:
            return None
        data = json.loads(json_str)
        if isinstance(data, dict) and "px_x" in data and "px_y" in data:
            return (int(data["px_x"]), int(data["px_y"]))
        return None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def is_in_viewport(coords: tuple[int, int], viewport: tuple[int, int]) -> bool:
    """座標がビューポート範囲内かチェックする。"""
    return 0 <= coords[0] <= viewport[0] and 0 <= coords[1] <= viewport[1]


# ──────────────────────────────────────────
# 画像ユーティリティ
# ──────────────────────────────────────────


def encode_png_b64(png_bytes: bytes) -> str:
    """PNG バイト列を Base64 文字列にエンコードする。"""
    return base64.b64encode(png_bytes).decode("ascii")


# ──────────────────────────────────────────
# Perceptual Hash (dHash) — 依存なし実装
# ──────────────────────────────────────────


def phash(png_bytes: bytes, size: int = 16) -> str:
    """PNG バイト列の dHash を 16 進文字列で返す（size x size ビット）。

    Pillow があればそれを使い、無ければ簡易フォールバックを返す。
    同じ画像は同じハッシュを返し、近い画像は Hamming 距離が小さくなる。
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        # 依存が無い場合は SHA1 のプレフィックスで近似（互換性優先）
        import hashlib

        return hashlib.sha1(png_bytes).hexdigest()[: (size * size) // 4 or 16]

    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("L").resize(
            (size + 1, size), Image.Resampling.LANCZOS
        )
        pixels = list(img.getdata())
        width = size + 1
        bits = []
        for y in range(size):
            row_start = y * width
            for x in range(size):
                bits.append(1 if pixels[row_start + x] > pixels[row_start + x + 1] else 0)
        # bits を 16 進文字列へ
        n = 0
        for b in bits:
            n = (n << 1) | b
        hex_len = (len(bits) + 3) // 4
        return format(n, f"0{hex_len}x")
    except Exception:
        import hashlib

        return hashlib.sha1(png_bytes).hexdigest()[: (size * size) // 4 or 16]


def phash_distance(a: str, b: str) -> int:
    """2 つの pHash 16 進文字列のハミング距離（ビット単位）を返す。

    長さが異なる場合は短い方を基準にするが、大きな値を返して「大きく異なる」扱いにする。
    """
    if not a or not b:
        return 10_000
    if len(a) != len(b):
        return 10_000
    try:
        ia = int(a, 16)
        ib = int(b, 16)
    except ValueError:
        return 10_000
    return (ia ^ ib).bit_count()


# ──────────────────────────────────────────
# 座標検証（ビューポート内 + 任意のスナップ）
# ──────────────────────────────────────────


def snap_to_grid(coords: tuple[int, int], grid_size: int) -> tuple[int, int]:
    """座標を最近接グリッドセル中心にスナップする。"""
    gx = round(coords[0] / grid_size)
    gy = round(coords[1] / grid_size)
    return (gx * grid_size + grid_size // 2, gy * grid_size + grid_size // 2)


def validate_coordinates(
    coords: list[dict[str, Any]],
    viewport: tuple[int, int],
    grid_size: int | None = None,
) -> list[dict[str, Any]]:
    """座標リストをビューポート内にフィルタし、任意でグリッドスナップする。

    `grid_size` が None の場合はスナップせず px_x/px_y のみ検証して返す。
    """
    valid: list[dict[str, Any]] = []
    for c in coords:
        try:
            px_x = int(c.get("px_x", 0))
            px_y = int(c.get("px_y", 0))
        except (TypeError, ValueError):
            continue
        if not is_in_viewport((px_x, px_y), viewport):
            continue
        item = dict(c)
        if grid_size:
            sx, sy = snap_to_grid((px_x, px_y), grid_size)
            item["px_x"] = sx
            item["px_y"] = sy
            item["grid_x"] = round(px_x / grid_size)
            item["grid_y"] = round(px_y / grid_size)
        else:
            item["px_x"] = px_x
            item["px_y"] = px_y
        valid.append(item)
    return valid
