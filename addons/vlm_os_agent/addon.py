"""VLM OS Agent — ToolAddon エントリポイント。

OS レベルでスクショを取って VLM で UI 要素を特定し、pyautogui で
マウス・キーボード操作を行う自律エージェント。

完全ローカル動作（LM Studio 経由のローカル VLM を使用）。
オプション依存（mss / pyautogui / pyperclip / pygetwindow / pynput / Pillow）は
`[vlm-agent]` extras にまとめられており、未導入でもアドオン自体のロードは成功する
（ツール呼び出し時に明確なエラーメッセージを返す）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext
from core.config import (
    get_vlm_agent_cache_ttl,
    get_vlm_agent_failsafe,
    get_vlm_agent_max_steps,
    get_vlm_agent_perceive_backend,
    get_vlm_agent_poll_ms,
    get_vlm_agent_som_enabled,
    get_vlm_agent_target_window,
)

logger = logging.getLogger(__name__)


VLM_OS_AGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "os_screenshot",
            "description": (
                "起動中のデスクトップアプリのスクリーンショットを取得する。"
                "window_title が指定されていればそのウィンドウをクロップ、"
                "省略時は .env の VLM_AGENT_TARGET_WINDOW にマッチするウィンドウを使う。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window_title": {
                        "type": "string",
                        "description": "対象ウィンドウタイトル（正規表現、省略可）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "os_find_element",
            "description": (
                "VLM で画面内の UI 要素の中心座標を特定する。"
                "use_cache は既定 false（明示指定時のみキャッシュを利用）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "要素の説明（例: チャット送信ボタン）",
                    },
                    "window_title": {"type": "string"},
                    "use_cache": {"type": "boolean", "default": False},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "os_click_element",
            "description": "UI 要素を特定して OS レベルでクリックする（効果なしなら 1 回リトライ）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "button": {"type": "string", "enum": ["left", "right"]},
                    "double": {"type": "boolean"},
                    "window_title": {"type": "string"},
                    "use_cache": {"type": "boolean", "default": False},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "os_type_text",
            "description": (
                "フォーカス中のフィールドに文字列を入力する。"
                "CJK 入力は use_clipboard=true（既定）で安全に送信する。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "use_clipboard": {"type": "boolean", "default": True},
                    "submit": {"type": "boolean", "default": False},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "os_run_task",
            "description": (
                "高レベルゴール（例: 『チャットで〇〇と送信』）を受け、"
                "VLM と内部ツールで複数ステップの自律実行を行う。"
                "ESC キーで即座に中断可能。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "window_title": {"type": "string"},
                    "max_steps": {"type": "integer"},
                    "use_cache": {"type": "boolean", "default": False},
                },
                "required": ["goal"],
            },
        },
    },
]


def _json_result(obj: Any) -> str:
    """dict / list などを JSON 文字列化（ensure_ascii=False）。"""
    return json.dumps(obj, ensure_ascii=False, default=str)


def _missing_extras_error() -> str:
    return _json_result({
        "error": "vlm-agent extras が未導入です",
        "hint": "pip install -e .[vlm-agent] を実行してください",
    })


class VlmOsAgentAddon(ToolAddon):
    """VLM 駆動 OS 級自律エージェント。"""

    def __init__(self) -> None:
        self._runtime: Any = None
        self._kill_switch: Any = None
        self._context: AddonContext | None = None
        self._settings: Any = None

    # ──────────────────────────────────────
    # ライフサイクル
    # ──────────────────────────────────────

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        self._settings = self._build_settings()
        # KillSwitch は pynput 未導入でも生成可能（start() で degrade ログのみ）
        try:
            from .kill_switch import KillSwitch
            self._kill_switch = KillSwitch()
            self._kill_switch.start()
        except Exception as e:
            logger.warning("KillSwitch 初期化失敗: %s", e)
            self._kill_switch = None
        logger.info(
            "VLM OS Agent をロードしました（対象ウィンドウ: '%s', SoM: %s, FAILSAFE: %s）",
            self._settings.target_window,
            self._settings.som_enabled,
            self._settings.failsafe,
        )

    def on_unload(self) -> None:
        if self._kill_switch is not None:
            try:
                self._kill_switch.stop()
            except Exception as e:
                logger.warning("KillSwitch 停止失敗: %s", e)
            self._kill_switch = None
        self._runtime = None

    def get_tools(self) -> list[dict]:
        return VLM_OS_AGENT_TOOLS

    def get_gui_tab_class(self) -> type | None:
        try:
            from .gui_tab import VlmOsAgentTab
            return VlmOsAgentTab
        except ImportError:
            return None

    # ──────────────────────────────────────
    # ツール実行
    # ──────────────────────────────────────

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        runtime = self._ensure_runtime()
        if runtime is None:
            return False, _missing_extras_error()

        try:
            if tool_name == "os_screenshot":
                frame = runtime.screenshot(
                    window_title=tool_args.get("window_title"),
                )
                return False, _json_result({
                    "width": frame.viewport[0],
                    "height": frame.viewport[1],
                    "phash": frame.phash,
                    "window_title": frame.window_title,
                    "bbox": list(frame.bbox),
                    "image_b64_len": len(frame.image_b64),
                })

            if tool_name == "os_find_element":
                result = runtime.find_element(
                    description=tool_args.get("description", ""),
                    window_title=tool_args.get("window_title"),
                    use_cache=bool(tool_args.get("use_cache", False)),
                )
                if result is None:
                    return False, _json_result({"ok": False, "reason": "not_found"})
                return False, _json_result({
                    "ok": True,
                    "px_x": result.px_x,
                    "px_y": result.px_y,
                    "confidence": result.confidence,
                    "cache_hit": result.cache_hit,
                    "mark_id": result.mark_id,
                })

            if tool_name == "os_click_element":
                r = runtime.click(
                    description=tool_args.get("description", ""),
                    button=tool_args.get("button", "left"),
                    double=bool(tool_args.get("double", False)),
                    window_title=tool_args.get("window_title"),
                    use_cache=bool(tool_args.get("use_cache", False)),
                )
                return False, _json_result(r)

            if tool_name == "os_type_text":
                r = runtime.type_text(
                    text=tool_args.get("text", ""),
                    use_clipboard=bool(tool_args.get("use_clipboard", True)),
                    submit=bool(tool_args.get("submit", False)),
                )
                return False, _json_result(r)

            if tool_name == "os_run_task":
                r = runtime.run_task(
                    goal=tool_args.get("goal", ""),
                    window_title=tool_args.get("window_title"),
                    max_steps=tool_args.get("max_steps"),
                    use_cache=bool(tool_args.get("use_cache", False)),
                )
                return False, _json_result(r)

            return False, _json_result({"error": f"未対応ツール: {tool_name}"})
        except Exception as e:
            logger.exception("VLM OS Agent ツール実行エラー: %s", tool_name)
            return False, _json_result({"error": str(e), "tool": tool_name})

    # ──────────────────────────────────────
    # 内部ヘルパ
    # ──────────────────────────────────────

    def _build_settings(self) -> Any:
        from .agent_runtime import AgentSettings
        return AgentSettings(
            target_window=get_vlm_agent_target_window(),
            poll_ms=get_vlm_agent_poll_ms(),
            cache_ttl=get_vlm_agent_cache_ttl(),
            som_enabled=get_vlm_agent_som_enabled(),
            max_steps=get_vlm_agent_max_steps(),
            failsafe=get_vlm_agent_failsafe(),
            perceive_backend=get_vlm_agent_perceive_backend(),
        )

    def _ensure_runtime(self) -> Any | None:
        """AgentRuntime を遅延生成する。オプション依存欠落なら None を返す。"""
        if self._runtime is not None:
            return self._runtime
        if self._context is None or self._kill_switch is None:
            return None
        try:
            from .agent_runtime import AgentRuntime
            self._runtime = AgentRuntime(
                lm_client=self._context.lm_client,
                configs_dir=self._context.root_dir / "configs",
                addon_dir=self.addon_dir,
                kill_switch=self._kill_switch,
                settings=self._settings,
            )
            return self._runtime
        except ImportError as e:
            logger.warning("AgentRuntime 初期化失敗（extras 未導入）: %s", e)
            return None
        except Exception as e:
            logger.exception("AgentRuntime 初期化エラー: %s", e)
            return None
