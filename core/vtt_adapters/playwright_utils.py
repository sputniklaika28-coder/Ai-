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


# ──────────────────────────────────────────
# ファイルアップロードヘルパー
# ──────────────────────────────────────────


def find_file_input(page: object) -> object | None:
    """ページ上の input[type="file"] を検索する（hidden 含む）。

    Args:
        page: Playwright Page オブジェクト。

    Returns:
        見つかった ElementHandle、なければ None。
    """
    try:
        el = page.query_selector('input[type="file"]')  # type: ignore[union-attr]
        return el
    except Exception:
        return None


def set_file_via_input(
    page: object, file_path: str, selector: str = 'input[type="file"]'
) -> bool:
    """Playwright の set_input_files でファイルを注入する。

    OS のファイルダイアログをバイパスし、直接ファイルパスを設定する。

    Args:
        page: Playwright Page オブジェクト。
        file_path: アップロードするファイルの絶対パス。
        selector: ファイル入力要素の CSS セレクタ。

    Returns:
        成功した場合 True。
    """
    try:
        page.set_input_files(selector, file_path)  # type: ignore[union-attr]
        logger.info("ファイルを注入: %s", file_path)
        return True
    except Exception as e:
        logger.error("ファイル注入エラー: %s", e)
        return False


# ──────────────────────────────────────────
# Canvas スクリーンショット・座標ヘルパー
# ──────────────────────────────────────────

_CANVAS_BOUNDS_JS = """() => {
    const canvas = document.querySelector('canvas');
    if (!canvas) {
        // canvas がなければメインのボード領域を探す
        const board = document.querySelector(
            '[class*="board"], [class*="field"], [class*="map"], .movable'
        );
        if (board) {
            const r = board.closest('[style*="transform"]')?.getBoundingClientRect()
                      || board.getBoundingClientRect();
            return {x: r.x, y: r.y, width: r.width, height: r.height};
        }
        return null;
    }
    const r = canvas.getBoundingClientRect();
    return {x: r.x, y: r.y, width: r.width, height: r.height};
}"""


def get_canvas_bounds(page: object) -> dict | None:
    """Canvas / ボード要素の境界矩形を取得する。

    Args:
        page: Playwright Page オブジェクト。

    Returns:
        {x, y, width, height} 辞書、見つからなければ None。
    """
    try:
        return page.evaluate(_CANVAS_BOUNDS_JS)  # type: ignore[union-attr]
    except Exception:
        return None


def clip_screenshot(page: object, bounds: dict) -> bytes | None:
    """指定領域だけをスクリーンショットする。

    Args:
        page: Playwright Page オブジェクト。
        bounds: {x, y, width, height} 辞書。

    Returns:
        PNG 画像のバイト列、失敗時は None。
    """
    try:
        return page.screenshot(  # type: ignore[union-attr]
            clip={
                "x": bounds["x"],
                "y": bounds["y"],
                "width": bounds["width"],
                "height": bounds["height"],
            }
        )
    except Exception as e:
        logger.error("クリップスクリーンショットエラー: %s", e)
        return None


def mouse_drag(
    page: object,
    from_xy: tuple[int, int],
    to_xy: tuple[int, int],
    steps: int = 10,
) -> bool:
    """ステップ分割のスムーズなマウスドラッグを実行する。

    Canvas 要素のように JS イベントディスパッチが効かない領域で、
    リアルなマウスイベントを発生させる。

    Args:
        page: Playwright Page オブジェクト。
        from_xy: ドラッグ開始座標 (x, y)。
        to_xy: ドラッグ終了座標 (x, y)。
        steps: 中間ステップ数（多いほど滑らか）。

    Returns:
        成功した場合 True。
    """
    try:
        mouse = page.mouse  # type: ignore[union-attr]
        mouse.move(from_xy[0], from_xy[1])
        mouse.down()
        for i in range(1, steps + 1):
            x = from_xy[0] + (to_xy[0] - from_xy[0]) * i / steps
            y = from_xy[1] + (to_xy[1] - from_xy[1]) * i / steps
            mouse.move(x, y)
        mouse.up()
        logger.info("ドラッグ完了: (%d,%d) → (%d,%d)", *from_xy, *to_xy)
        return True
    except Exception as e:
        logger.error("ドラッグエラー: %s", e)
        return False
