"""playwright_utils.py — Playwright 共通ユーティリティ。

CCFoliaAdapter と BrowserUseVTTAdapter の両方で使用する
DOM 解析・駒操作のヘルパー関数を集約する。
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

GRID_SIZE = 96

# ──────────────────────────────────────────
# 座標ユーティリティ
# ──────────────────────────────────────────


def parse_xy(style: str | None) -> tuple[int, int]:
    """CSS transform translate(Xpx, Ypx) からピクセル座標を抽出する。"""
    m = re.search(r"translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)", style or "")
    return (int(float(m.group(1))), int(float(m.group(2)))) if m else (0, 0)


def extract_hash(url: str | None) -> str:
    """CCFolia画像URLから8文字ハッシュを抽出する。"""
    m = re.search(r"/(?:shared|files)/([a-f0-9]+)", url or "")
    return m.group(1)[:8] if m else ""


# ──────────────────────────────────────────
# Playwright Page 操作ヘルパー
# ──────────────────────────────────────────

_BOARD_STATE_JS = """() => {
    const out = [];
    document.querySelectorAll('.movable').forEach((el, i) => {
        const t = el.style.transform;
        if (!t || !t.includes('translate(')) return;
        const img = el.querySelector('img');
        const r = el.getBoundingClientRect();
        out.push({
            index: i, transform: t,
            imgSrc: img ? img.src : '',
            vx: r.left + r.width/2,
            vy: r.top  + r.height/2
        });
    });
    return out;
}"""


def get_board_state_from_page(page: object) -> list[dict]:
    """Playwright Page から全駒の位置情報を取得する。

    Args:
        page: Playwright Page オブジェクト。

    Returns:
        駒情報のリスト。
    """
    raw = page.evaluate(_BOARD_STATE_JS)  # type: ignore[union-attr]
    result: list[dict] = []
    for p in raw:
        px_x, px_y = parse_xy(p["transform"])
        result.append({
            "index": p["index"],
            "img_hash": extract_hash(p["imgSrc"]),
            "img_url": p["imgSrc"],
            "px_x": px_x,
            "px_y": px_y,
            "grid_x": round(px_x / GRID_SIZE),
            "grid_y": round(px_y / GRID_SIZE),
        })
    return result


def spawn_piece_clipboard(page: object, character_json: dict) -> bool:
    """キャラクターJSONをクリップボード経由でCCFoliaにペーストして配置する。

    Args:
        page: Playwright Page オブジェクト。
        character_json: VTTプラットフォーム形式のキャラクターデータ。

    Returns:
        配置が成功した場合 True。
    """
    try:
        json_text = json.dumps(character_json, ensure_ascii=False)
        page.evaluate("(text) => navigator.clipboard.writeText(text)", json_text)  # type: ignore[union-attr]
        page.keyboard.press("Escape")  # type: ignore[union-attr]
        page.wait_for_timeout(200)  # type: ignore[union-attr]
        body = page.query_selector("body")  # type: ignore[union-attr]
        if body:
            body.click()
        page.keyboard.press("Control+v")  # type: ignore[union-attr]
        page.wait_for_timeout(500)  # type: ignore[union-attr]
        logger.info("駒を配置しました")
        return True
    except Exception as e:
        logger.error("駒配置エラー: %s", e)
        return False
