"""agent_runtime.py — VLM OS Agent のコアランタイム。

Sense → Perceive → Think → Act ループを提供する。
- スクリーンショット取得（screen.py）
- SoM 番号札オーバーレイ（perceive.py, Phase 3）
- VLM 座標特定（core.lm_client.LMClient + configs/browser_use_tasks.json）
- 座標キャッシュ（coord_cache.py, 既定 OFF）
- OS 入力（actuator.py）
- 実行後 pHash 差分検証 → 変化なしならキャッシュ破棄＋1 回リトライ
- キルスイッチ協調停止
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.vision_utils import (
    encode_png_b64,
    is_in_viewport,
    load_vlm_templates,
    parse_single_coordinate,
    phash,
    phash_distance,
)

from .actuator import Actuator
from .coord_cache import CoordCache
from .kill_switch import AgentCancelled, KillSwitch
from .perceive import PerceiveResult, annotate, find_mark
from .screen import CapturedImage, ScreenCaptureError, capture
from .window_focus import WindowInfo, find_window, focus, get_bbox

logger = logging.getLogger(__name__)


# ──────────────────────────────────────
# データクラス
# ──────────────────────────────────────


@dataclass(frozen=True)
class AgentSettings:
    """アドオン設定のスナップショット（.env から生成）。"""

    target_window: str
    poll_ms: int
    cache_ttl: int
    som_enabled: bool
    max_steps: int
    failsafe: bool
    perceive_backend: str = "none"


@dataclass
class ScreenshotFrame:
    """Sense 段階で得られる画面情報。"""

    image_b64: str
    png_bytes: bytes
    phash: str
    bbox: tuple[int, int, int, int]
    viewport: tuple[int, int]
    window_title: str
    perceive: PerceiveResult | None = None


@dataclass
class FindResult:
    """find_element の結果。"""

    px_x: int
    px_y: int
    confidence: float
    cache_hit: bool
    mark_id: int | None = None


# ──────────────────────────────────────
# LMClient の同期ラッパ
# ──────────────────────────────────────


def _run_coro_sync(coro: Any, timeout: float | None = None) -> Any:
    """coroutine を同期的に実行する。既存ループがある環境でもブロックしない。"""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is None:
        return asyncio.run(coro)
    # 別スレッドで新しいイベントループを作って実行
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(asyncio.run, coro)
        return fut.result(timeout=timeout)


# ──────────────────────────────────────
# AgentRuntime
# ──────────────────────────────────────


class AgentRuntime:
    """VLM OS Agent の中核ランタイム。アドオンから単一インスタンスで生成される。"""

    def __init__(
        self,
        lm_client: Any,
        configs_dir: Path,
        addon_dir: Path,
        kill_switch: KillSwitch,
        settings: AgentSettings,
    ) -> None:
        self._lm = lm_client
        self._configs_dir = Path(configs_dir)
        self._addon_dir = Path(addon_dir)
        self._ks = kill_switch
        self._settings = settings
        self._templates = load_vlm_templates(self._configs_dir)
        cache_path = self._addon_dir / "cache" / "coords.json"
        self._cache = CoordCache(cache_path, ttl_seconds=settings.cache_ttl)
        self._actuator = Actuator(
            kill_switch=kill_switch,
            failsafe=settings.failsafe,
        )
        self._lock = threading.Lock()
        self._last_window: WindowInfo | None = None

    # ──────────────────────────────────────
    # Sense
    # ──────────────────────────────────────

    def _resolve_window(
        self, window_title: str | None
    ) -> tuple[WindowInfo | None, str]:
        """ウィンドウ特定＋前面化。見つからなければ全画面扱い。"""
        title_pattern = window_title or self._settings.target_window
        info = find_window(title_pattern) if title_pattern else None
        if info is not None:
            focus(info)
            self._last_window = info
            return info, info.title
        if title_pattern:
            logger.info(
                "対象ウィンドウ '%s' が見つからないため全画面で動作します",
                title_pattern,
            )
        return None, title_pattern or ""

    def screenshot(
        self,
        window_title: str | None = None,
    ) -> ScreenshotFrame:
        """現在画面のスナップショットを取得する。"""
        self._ks.raise_if_set()
        info, title = self._resolve_window(window_title)
        bbox = get_bbox(info) if info else None
        try:
            img: CapturedImage = capture(bbox=bbox)
        except ScreenCaptureError:
            raise
        h = phash(img.png_bytes)
        perceive_res: PerceiveResult | None = None
        if self._settings.som_enabled:
            backend = self._settings.perceive_backend or "cv"
            perceive_res = annotate(img.png_bytes, backend=backend)
            img_png_for_vlm = perceive_res.annotated_png
        else:
            img_png_for_vlm = img.png_bytes
        return ScreenshotFrame(
            image_b64=encode_png_b64(img_png_for_vlm),
            png_bytes=img.png_bytes,
            phash=h,
            bbox=img.bbox,
            viewport=img.viewport,
            window_title=title,
            perceive=perceive_res,
        )

    # ──────────────────────────────────────
    # VLM 呼び出し
    # ──────────────────────────────────────

    def _call_vlm(self, prompt: str, image_b64: str, timeout: float = 60.0) -> str:
        """LMClient.generate_with_tools を同期呼び出しする（ローカル専用）。"""
        self._ks.raise_if_set()
        messages = [{"role": "user", "content": prompt}]
        try:
            result = _run_coro_sync(
                self._lm.generate_with_tools(
                    messages=messages,
                    tools=[],
                    temperature=0.2,
                    max_tokens=800,
                    image_base64=image_b64,
                ),
                timeout=timeout,
            )
        except Exception as e:
            logger.error("VLM 呼び出しエラー: %s", e)
            return ""
        if not isinstance(result, tuple) or len(result) != 2:
            return ""
        content, _tool_calls = result
        return content or ""

    # ──────────────────────────────────────
    # Perceive/Think: find_element
    # ──────────────────────────────────────

    def find_element(
        self,
        description: str,
        *,
        frame: ScreenshotFrame | None = None,
        window_title: str | None = None,
        use_cache: bool = False,
    ) -> FindResult | None:
        """UI 要素の絶対ピクセル座標を特定する。"""
        self._ks.raise_if_set()
        if frame is None:
            frame = self.screenshot(window_title=window_title)

        # 1) キャッシュ参照（明示 use_cache=True のみ）
        if use_cache:
            cached = self._cache.get(frame.window_title, frame.phash, description)
            if cached is not None:
                px_x, px_y = cached
                abs_x, abs_y = self._to_absolute_xy(px_x, px_y, frame)
                return FindResult(
                    px_x=abs_x,
                    px_y=abs_y,
                    confidence=0.9,
                    cache_hit=True,
                )

        # 2) SoM 有効かつ marks がある場合は ID ベース
        if frame.perceive and frame.perceive.marks:
            mark = self._find_element_via_som(description, frame)
            if mark is not None:
                abs_x, abs_y = self._to_absolute_xy(mark.center[0], mark.center[1], frame)
                result = FindResult(
                    px_x=abs_x,
                    px_y=abs_y,
                    confidence=0.75,
                    cache_hit=False,
                    mark_id=mark.id,
                )
                if use_cache:
                    self._cache.put(
                        frame.window_title, frame.phash, description,
                        (mark.center[0], mark.center[1]),
                    )
                return result

        # 3) 素通しで VLM に座標を聞く
        prompt_tpl = self._templates.get("os_find_element", "")
        if not prompt_tpl:
            logger.warning("os_find_element プロンプトテンプレートが見つかりません")
            return None
        prompt = prompt_tpl.format(
            description=description,
            width=frame.viewport[0],
            height=frame.viewport[1],
        )
        response = self._call_vlm(prompt, frame.image_b64)
        coords = parse_single_coordinate(response)
        if coords is None:
            return None
        if coords == (-1, -1):
            return None
        if not is_in_viewport(coords, frame.viewport):
            logger.info("VLM 返却座標がビューポート外: %s", coords)
            return None

        abs_x, abs_y = self._to_absolute_xy(coords[0], coords[1], frame)
        if use_cache:
            self._cache.put(
                frame.window_title, frame.phash, description, coords,
            )
        return FindResult(
            px_x=abs_x, px_y=abs_y, confidence=0.6, cache_hit=False,
        )

    def _find_element_via_som(
        self, description: str, frame: ScreenshotFrame
    ) -> Any | None:
        """SoM（番号札）モードで Mark を特定する。"""
        if not frame.perceive:
            return None
        prompt_tpl = self._templates.get("os_find_element_som", "")
        if not prompt_tpl:
            return None
        prompt = prompt_tpl.format(description=description)
        response = self._call_vlm(prompt, frame.image_b64)
        try:
            from core.vision_utils import extract_json
            raw = extract_json(response)
            if not raw:
                return None
            data = json.loads(raw)
            mark_id = int(data.get("mark_id", -1))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        if mark_id <= 0:
            return None
        return find_mark(frame.perceive.marks, mark_id)

    def _to_absolute_xy(
        self, rel_x: int, rel_y: int, frame: ScreenshotFrame
    ) -> tuple[int, int]:
        """画像内相対座標を画面絶対座標に変換する。"""
        return (frame.bbox[0] + int(rel_x), frame.bbox[1] + int(rel_y))

    # ──────────────────────────────────────
    # Act: click / type_text
    # ──────────────────────────────────────

    def click(
        self,
        description: str,
        *,
        button: str = "left",
        double: bool = False,
        window_title: str | None = None,
        use_cache: bool = False,
    ) -> dict[str, Any]:
        """要素を特定してクリック。pHash 差分で失敗検出＆1 回リトライ。"""
        self._ks.raise_if_set()
        pre_frame = self.screenshot(window_title=window_title)
        target = self.find_element(
            description, frame=pre_frame, use_cache=use_cache,
        )
        if target is None:
            return {"ok": False, "reason": "element_not_found", "retried": False}

        self._actuator.move_to(target.px_x, target.px_y, duration=0.12)
        if double:
            self._actuator.double_click(target.px_x, target.px_y, button=button)
        else:
            self._actuator.click(target.px_x, target.px_y, button=button)

        # 実行後の差分検証
        time.sleep(max(0.1, self._settings.poll_ms / 1000.0))
        post_frame = self.screenshot(window_title=window_title)
        dist = phash_distance(pre_frame.phash, post_frame.phash)

        result: dict[str, Any] = {
            "ok": True,
            "px_x": target.px_x,
            "px_y": target.px_y,
            "retried": False,
            "cache_hit": target.cache_hit,
            "phash_diff": dist,
        }

        # 画面が全く変わっていなければ「効かなかった」と判断し、キャッシュ破棄＋1 回再試行
        if dist <= 1 and target.cache_hit:
            logger.info("クリック後に画面変化なし → キャッシュ破棄して再特定します")
            self._cache.invalidate_match(
                pre_frame.window_title, pre_frame.phash, description,
            )
            retry_target = self.find_element(
                description, frame=post_frame, use_cache=False,
            )
            if retry_target is None:
                result["ok"] = False
                result["reason"] = "retry_find_failed"
                result["retried"] = True
                return result
            self._actuator.move_to(retry_target.px_x, retry_target.px_y, duration=0.12)
            if double:
                self._actuator.double_click(
                    retry_target.px_x, retry_target.px_y, button=button,
                )
            else:
                self._actuator.click(
                    retry_target.px_x, retry_target.px_y, button=button,
                )
            result["px_x"] = retry_target.px_x
            result["px_y"] = retry_target.px_y
            result["retried"] = True
        return result

    def type_text(
        self,
        text: str,
        *,
        use_clipboard: bool = True,
        submit: bool = False,
    ) -> dict[str, Any]:
        """文字列を入力する（CJK は use_clipboard=True が安全）。"""
        self._ks.raise_if_set()
        if use_clipboard:
            self._actuator.type_clipboard(text, submit=submit)
        else:
            self._actuator.type_ascii(text, submit=submit)
        return {"ok": True, "submitted": bool(submit)}

    # ──────────────────────────────────────
    # Think: run_task
    # ──────────────────────────────────────

    _INNER_TOOLS: list[dict] = [
        {
            "type": "function",
            "function": {
                "name": "os_find_element",
                "description": "画面内の UI 要素の中心座標を VLM で特定する",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                    },
                    "required": ["description"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "os_click_element",
                "description": "画面内の UI 要素を特定してクリックする",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "button": {"type": "string", "enum": ["left", "right"]},
                        "double": {"type": "boolean"},
                    },
                    "required": ["description"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "os_type_text",
                "description": "文字列を入力する（CJK はクリップボード経由）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "use_clipboard": {"type": "boolean"},
                        "submit": {"type": "boolean"},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "task_done",
                "description": "ゴール達成、または続行不能のためタスクを終了する",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["success"],
                },
            },
        },
    ]

    def run_task(
        self,
        goal: str,
        *,
        window_title: str | None = None,
        max_steps: int | None = None,
        use_cache: bool = False,
    ) -> dict[str, Any]:
        """高レベル指示を受け、画面を見ながら複数ステップで自律実行する。"""
        self._ks.reset()
        if max_steps is None:
            max_steps = self._settings.max_steps
        steps: list[dict[str, Any]] = []
        system_prompt_tpl = self._templates.get("os_agent_plan_step", "")
        system_text = system_prompt_tpl.format(
            goal=goal, window_title=window_title or self._settings.target_window,
        ) if system_prompt_tpl else (
            f"ゴール: {goal}\n画面を見ながら必要な内部ツールを選んで実行してください。"
        )

        try:
            for step_idx in range(int(max_steps)):
                self._ks.raise_if_set()
                frame = self.screenshot(window_title=window_title)
                user_msg = (
                    f"ステップ {step_idx + 1}/{max_steps}。"
                    "現画面を見て次のアクションを function_call で 1 つだけ決めてください。"
                )
                messages = [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_msg},
                ]
                try:
                    result = _run_coro_sync(
                        self._lm.generate_with_tools(
                            messages=messages,
                            tools=self._INNER_TOOLS,
                            temperature=0.2,
                            max_tokens=600,
                            image_base64=frame.image_b64,
                        ),
                    )
                except Exception as e:
                    steps.append({"error": f"llm_error: {e}"})
                    break
                if not isinstance(result, tuple) or len(result) != 2:
                    steps.append({"error": "llm_empty_response"})
                    break
                content, tool_calls = result

                if not tool_calls:
                    steps.append({
                        "note": "no_tool_call",
                        "llm_text": (content or "")[:200],
                    })
                    # ツールを選ばない＝タスク終了とみなす
                    break

                call = tool_calls[0]
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}

                step_record: dict[str, Any] = {
                    "index": step_idx,
                    "tool": name,
                    "args": args,
                }

                if name == "task_done":
                    step_record["result"] = {
                        "success": bool(args.get("success", True)),
                        "reason": args.get("reason", ""),
                    }
                    steps.append(step_record)
                    return {
                        "ok": True,
                        "steps": steps,
                        "final_screen_phash": frame.phash,
                        "success": bool(args.get("success", True)),
                    }
                if name == "os_find_element":
                    res = self.find_element(
                        description=args.get("description", ""),
                        frame=frame,
                        use_cache=use_cache,
                    )
                    step_record["result"] = (
                        None if res is None else {
                            "px_x": res.px_x, "px_y": res.px_y,
                            "cache_hit": res.cache_hit,
                        }
                    )
                elif name == "os_click_element":
                    step_record["result"] = self.click(
                        description=args.get("description", ""),
                        button=args.get("button", "left"),
                        double=bool(args.get("double", False)),
                        window_title=window_title,
                        use_cache=use_cache,
                    )
                elif name == "os_type_text":
                    step_record["result"] = self.type_text(
                        text=args.get("text", ""),
                        use_clipboard=bool(args.get("use_clipboard", True)),
                        submit=bool(args.get("submit", False)),
                    )
                else:
                    step_record["result"] = {"error": f"unknown_tool: {name}"}
                steps.append(step_record)

            return {
                "ok": True,
                "steps": steps,
                "reason": "max_steps_reached",
            }
        except AgentCancelled:
            return {
                "ok": False,
                "cancelled": True,
                "reason": "esc",
                "steps": steps,
            }

    # ──────────────────────────────────────
    # 付帯アクセサ
    # ──────────────────────────────────────

    @property
    def cache(self) -> CoordCache:
        return self._cache

    @property
    def actuator(self) -> Actuator:
        return self._actuator


