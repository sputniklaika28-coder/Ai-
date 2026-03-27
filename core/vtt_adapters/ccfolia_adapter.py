"""ccfolia_adapter.py — CCFolia VTT用 Playwright アダプター（sync_api）。

pyautogui / Selenium を完全排除し、Playwright の sync_api で
CCFolia のブラウザ操作を実現する。

駒の配置は「クリップボード経由の Ctrl+V ペースト」ハックを活用し、
物理的なマウス操作やDPI計算を一切行わない。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

try:
    from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

    _HAS_PLAYWRIGHT = True
except ModuleNotFoundError:
    _HAS_PLAYWRIGHT = False
    Browser = BrowserContext = Page = sync_playwright = None  # type: ignore[assignment,misc]

try:
    from core.vtt_adapters.base_adapter import BaseVTTAdapter
except ModuleNotFoundError:
    from vtt_adapters.base_adapter import BaseVTTAdapter

logger = logging.getLogger(__name__)

GRID_SIZE = 96

# CCFolia の CSS セレクタ定数
_CHAT_INPUT = "textarea"
_CHAT_MESSAGES = "div.MuiListItemText-root"
_PIECE_SELECT = "[class*='MuiBox']"
_PIECE_ITEM = "li, [role='option'], [role='menuitem']"
_MOVABLE = ".movable"


class CCFoliaAdapter(BaseVTTAdapter):
    """CCFolia 専用の Playwright ベース VTT アダプター。"""

    def __init__(self) -> None:
        self._pw_context_manager: object | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> Page:
        """アクティブな Playwright Page を返す。未接続時はエラー。"""
        if self._page is None:
            raise RuntimeError("CCFoliaAdapter: connect() が呼ばれていません")
        return self._page

    # ──────────────────────────────────────────
    # 接続 / 切断
    # ──────────────────────────────────────────

    def connect(self, room_url: str, headless: bool = False) -> None:
        """Chromium を起動して CCFolia ルームに接続する。"""
        if not _HAS_PLAYWRIGHT:
            raise ModuleNotFoundError(
                "playwright パッケージが見つかりません。\n"
                "  pip install playwright && python -m playwright install chromium\n"
                "を実行してください。"
            )
        profile_dir = Path.home() / "AppData/Local/TacticalAI/PlaywrightProfile_AI"
        profile_dir.mkdir(parents=True, exist_ok=True)

        self._pw_context_manager = sync_playwright().start()
        pw = self._pw_context_manager

        self._browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=ja",
            ],
        )

        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
            permissions=["clipboard-read", "clipboard-write"],
        )

        # webdriver プロパティを隠蔽
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        self._page = self._context.new_page()
        self._page.goto(room_url, wait_until="domcontentloaded")

        logger.info("CCFolia に接続: %s", room_url)

        # チャット入力欄の出現を待機（最大30秒）
        try:
            self._page.wait_for_selector(_CHAT_INPUT, timeout=30_000)
            logger.info("チャット入力欄を確認")
            print("   ✓ チャット入力欄を検出")
        except Exception:
            logger.warning("チャット入力欄が見つかりません（ログインや入室が必要かもしれません）")
            print("   ⚠ チャット入力欄が見つかりません（ログインや入室が必要かもしれません）")

        # チャットメッセージ要素の存在確認（複数手法で診断）
        try:
            msgs = self._page.query_selector_all(_CHAT_MESSAGES)
            print(f"   DEBUG: 主セレクタ({_CHAT_MESSAGES})={len(msgs)}件")

            # JS抽出も試行
            js_msgs = self.get_chat_messages()
            print(f"   DEBUG: JS抽出メッセージ数={len(js_msgs)}件")
            if js_msgs:
                sample = js_msgs[-1]
                print(f"   DEBUG: 最新メッセージ例: [{sample['speaker']}] {sample['body'][:40]}")

            if not msgs and not js_msgs:
                print("   ⚠ チャットメッセージが検出できません。")
                print("   ⚠ CCFoliaにログイン済み・入室済みか確認してください。")
                # DOM構造の診断情報を出力
                diag = self._page.evaluate("""() => {
                    const info = {};
                    // スクロール領域の検出
                    const scrollAreas = document.querySelectorAll(
                        'div[style*="overflow"], [class*="scroll"], [class*="list"]'
                    );
                    info.scrollAreas = scrollAreas.length;
                    // textarea の数
                    info.textareas = document.querySelectorAll('textarea').length;
                    // role=listitem の数
                    info.listItems = document.querySelectorAll('[role="listitem"]').length;
                    // 主要なクラス名
                    const els = document.querySelectorAll('div[class]');
                    const classes = new Set();
                    for (let i = 0; i < Math.min(els.length, 300); i++) {
                        els[i].className.split(' ').forEach(c => { if (c) classes.add(c); });
                    }
                    info.sampleClasses = Array.from(classes).slice(0, 60).join(', ');
                    return info;
                }""")
                print(f"   DEBUG: DOM診断: textareas={diag.get('textareas')}, "
                      f"listItems={diag.get('listItems')}, scrollAreas={diag.get('scrollAreas')}")
                print(f"   DEBUG: CSSクラス(一部): {diag.get('sampleClasses', 'N/A')}")
        except Exception as e:
            print(f"   DEBUG: メッセージ要素チェック失敗: {e}")

    def close(self) -> None:
        """ブラウザを閉じて接続を切断する。"""
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw_context_manager and hasattr(self._pw_context_manager, "stop"):
                self._pw_context_manager.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._pw_context_manager = None

    # ──────────────────────────────────────────
    # ボード状態取得
    # ──────────────────────────────────────────

    def get_board_state(self) -> list[dict]:
        """全駒の位置情報を取得する。"""
        raw = self.page.evaluate("""() => {
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
        }""")

        result: list[dict] = []
        for p in raw:
            px_x, px_y = self._parse_xy(p["transform"])
            result.append({
                "index": p["index"],
                "img_hash": self._extract_hash(p["imgSrc"]),
                "img_url": p["imgSrc"],
                "px_x": px_x,
                "px_y": px_y,
                "grid_x": round(px_x / GRID_SIZE),
                "grid_y": round(px_y / GRID_SIZE),
            })
        return result

    # ──────────────────────────────────────────
    # 駒移動
    # ──────────────────────────────────────────

    def move_piece(self, piece_id: str, grid_x: int, grid_y: int) -> bool:
        """img_hash で駒を特定し、Playwright のドラッグ操作でグリッド座標に移動する。"""
        state = self.get_board_state()
        targets = [p for p in state if piece_id in p.get("img_url", "")]
        if not targets:
            logger.warning("駒が見つかりません: %s", piece_id)
            return False

        target = targets[0]
        src_px_x, src_px_y = target["px_x"], target["px_y"]
        dst_px_x, dst_px_y = grid_x * GRID_SIZE, grid_y * GRID_SIZE
        delta_x = dst_px_x - src_px_x
        delta_y = dst_px_y - src_px_y

        # Playwright の JS ベースドラッグで移動
        # DOM 要素を特定してマウスイベントをディスパッチする
        moved = self.page.evaluate(
            """([index, deltaX, deltaY]) => {
                const els = document.querySelectorAll('.movable');
                const el = els[index];
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;

                const opts = {bubbles: true, cancelable: true, clientX: cx, clientY: cy};
                el.dispatchEvent(new PointerEvent('pointerdown', opts));
                el.dispatchEvent(new MouseEvent('mousedown', opts));

                const moveOpts = {
                    bubbles: true, cancelable: true,
                    clientX: cx + deltaX, clientY: cy + deltaY
                };
                el.dispatchEvent(new PointerEvent('pointermove', moveOpts));
                el.dispatchEvent(new MouseEvent('mousemove', moveOpts));

                const upOpts = {
                    bubbles: true, cancelable: true,
                    clientX: cx + deltaX, clientY: cy + deltaY
                };
                el.dispatchEvent(new PointerEvent('pointerup', upOpts));
                el.dispatchEvent(new MouseEvent('mouseup', upOpts));
                return true;
            }""",
            [target["index"], delta_x, delta_y],
        )
        if moved:
            logger.info(
                "駒移動完了: %s → (%d, %d)", piece_id, grid_x, grid_y
            )
        return bool(moved)

    # ──────────────────────────────────────────
    # 駒配置（クリップボードハック）
    # ──────────────────────────────────────────

    def spawn_piece(self, character_json: dict) -> bool:
        """キャラクターJSONをクリップボード経由でCCFoliaにペーストして配置する。"""
        try:
            json_text = json.dumps(character_json, ensure_ascii=False)
            # クリップボードにJSONをセット
            self.page.evaluate(
                "(text) => navigator.clipboard.writeText(text)", json_text
            )
            # ボード領域にフォーカスしてペースト
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
            body = self.page.query_selector("body")
            if body:
                body.click()
            self.page.keyboard.press("Control+v")
            self.page.wait_for_timeout(500)
            logger.info("駒を配置しました")
            return True
        except Exception as e:
            logger.error("駒配置エラー: %s", e)
            return False

    # ──────────────────────────────────────────
    # チャット操作
    # ──────────────────────────────────────────

    def send_chat(self, character_name: str, text: str) -> bool:
        """CCFolia のチャットにメッセージを送信する。

        既存の _post_message ロジックを Playwright に移植。
        Shift+Enter で改行、最後に Enter で送信する。
        """
        try:
            # ダイアログを閉じる
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)

            # 駒選択（キャラクター切り替え）
            try:
                piece_select = self.page.query_selector(_PIECE_SELECT)
                if piece_select:
                    piece_select.click()
                    self.page.wait_for_timeout(300)
                    items = self.page.query_selector_all(_PIECE_ITEM)
                    for item in items:
                        item_text = item.text_content() or ""
                        if character_name in item_text:
                            item.click()
                            self.page.wait_for_timeout(200)
                            break
                    else:
                        self.page.keyboard.press("Escape")
            except Exception:
                pass

            self.page.wait_for_timeout(200)

            # チャット入力欄を取得
            inputs = self.page.query_selector_all(_CHAT_INPUT)
            if not inputs:
                logger.error("チャット入力欄が見つかりません")
                return False

            # 名前欄があれば設定
            if len(inputs) >= 2:
                inputs[0].click()
                inputs[0].press("Control+a")
                inputs[0].fill(character_name)

            input_el = inputs[-1]
            input_el.click()
            input_el.press("Control+a")
            input_el.press("Backspace")
            self.page.wait_for_timeout(100)

            # 改行は Shift+Enter、最後に Enter で送信
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line:
                    input_el.type(line, delay=10)
                if i < len(lines) - 1:
                    input_el.press("Shift+Enter")

            self.page.wait_for_timeout(300)
            input_el.press("Enter")
            self.page.wait_for_timeout(500)
            return True

        except Exception as e:
            logger.error("チャット送信エラー: %s", e)
            return False

    def get_chat_messages(self) -> list[dict]:
        """チャットメッセージ一覧を取得する。"""
        messages: list[dict] = []
        try:
            # まず JavaScript 評価で直接メッセージを抽出（DOM構造に依存しにくい）
            js_messages = self._extract_messages_via_js()
            if js_messages:
                return js_messages

            # JS抽出が空ならCSSセレクタベースのフォールバック
            selectors = [
                _CHAT_MESSAGES,
                "[class*='ChatMessage']",
                "[class*='chatMessage']",
                "[class*='message-list'] > div",
                "div[class*='MuiList'] div[class*='MuiListItem']",
                "div[class*='chat'] div[class*='item']",
                "div[class*='log'] > div",
            ]
            items = []
            for sel in selectors:
                items = self.page.query_selector_all(sel)
                if items:
                    logger.info("CSSセレクタで検出: %s (%d件)", sel, len(items))
                    break

            if not items:
                logger.debug("チャット要素が見つかりません")
                return messages

            for el in items:
                parsed = self._parse_chat_element(el)
                if parsed:
                    messages.append(parsed)
        except Exception as e:
            logger.error("get_chat_messages エラー: %s", e)
        return messages

    def _extract_messages_via_js(self) -> list[dict]:
        """JavaScriptでDOMを走査してチャットメッセージを抽出する。

        CCFoliaのDOM構造変更に対してCSSセレクタより堅牢。
        チャットログ領域を自動検出し、各メッセージから発言者と本文を取得する。
        """
        try:
            raw = self.page.evaluate("""() => {
                const results = [];
                const _SKIP = new Set(["メイン", "情報", "noname"]);

                // 戦略1: MuiListItemText（従来のセレクタ）
                let items = document.querySelectorAll('div.MuiListItemText-root');

                // 戦略2: role=listitem 内のテキスト要素
                if (!items.length) {
                    items = document.querySelectorAll('[role="listitem"]');
                }

                // 戦略3: チャットログ風の長いスクロール領域内の子要素
                if (!items.length) {
                    const scrollAreas = document.querySelectorAll(
                        'div[style*="overflow"], [class*="scroll"], [class*="list"], [class*="log"], [class*="chat"]'
                    );
                    for (const area of scrollAreas) {
                        if (area.scrollHeight > 300 && area.children.length > 2) {
                            items = area.children;
                            break;
                        }
                    }
                }

                if (!items || !items.length) return results;

                for (const el of items) {
                    const text = (el.textContent || "").trim();
                    if (!text) continue;

                    const lines = text.split(/\\n/).map(l => l.trim()).filter(Boolean);
                    if (lines.length < 2) continue;

                    const speaker = lines[0];
                    const body = lines.slice(1).join(" ");

                    if (_SKIP.has(speaker)) continue;
                    if (speaker.includes("[AI]")) continue;
                    if (body.length < 1) continue;

                    results.push({speaker: speaker, body: body});
                }
                return results;
            }""")
            if raw and len(raw) > 0:
                return raw
        except Exception as e:
            logger.debug("JS メッセージ抽出エラー: %s", e)
        return []

    @staticmethod
    def _parse_chat_element(el) -> dict | None:
        """単一のチャット要素からspeakerとbodyを抽出する。"""
        _SKIP = {"メイン", "情報", "noname"}
        try:
            text = el.text_content() or ""
            lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
            if len(lines) >= 2:
                speaker, body = lines[0], " ".join(lines[1:])
                if speaker not in _SKIP and "[AI]" not in speaker:
                    return {"speaker": speaker, "body": body}
        except Exception:
            pass
        return None

    # ──────────────────────────────────────────
    # スクリーンショット
    # ──────────────────────────────────────────

    def take_screenshot(self) -> bytes | None:
        """画面のスクリーンショットをPNGバイト列で取得する。"""
        try:
            return self.page.screenshot()
        except Exception:
            return None

    # ──────────────────────────────────────────
    # マップスクロール
    # ──────────────────────────────────────────

    def pan_map(self, direction: str, grid_amount: int = 1) -> bool:
        """矢印キーでマップをスクロールする。"""
        key_map = {
            "up": "ArrowUp",
            "down": "ArrowDown",
            "left": "ArrowLeft",
            "right": "ArrowRight",
        }
        key = key_map.get(direction)
        if not key:
            return False
        try:
            # マップ領域にフォーカス
            map_el = self.page.query_selector(
                '[class*="map"],[class*="board"],[class*="field"]'
            )
            if map_el:
                map_el.click()
            else:
                body = self.page.query_selector("body")
                if body:
                    body.click()

            for _ in range(grid_amount):
                self.page.keyboard.press(key)
                self.page.wait_for_timeout(30)
            return True
        except Exception as e:
            logger.error("マップスクロールエラー: %s", e)
            return False

    # ──────────────────────────────────────────
    # ユーティリティ
    # ──────────────────────────────────────────

    @staticmethod
    def _parse_xy(style: str | None) -> tuple[int, int]:
        """CSS transform translate(Xpx, Ypx) からピクセル座標を抽出する。"""
        m = re.search(r"translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)", style or "")
        return (int(float(m.group(1))), int(float(m.group(2)))) if m else (0, 0)

    @staticmethod
    def _extract_hash(url: str | None) -> str:
        """CCFolia画像URLから8文字ハッシュを抽出する。"""
        m = re.search(r"/(?:shared|files)/([a-f0-9]+)", url or "")
        return m.group(1)[:8] if m else ""
