"""agent_runtime.py のユニットテスト。

外部依存（LMClient / mss / pyautogui / pyperclip / pygetwindow / pynput / PIL）は
すべて mock 化して、ピュアなロジックだけを検証する。
"""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ──────────────────────────────────────
# 共通 fixture
# ──────────────────────────────────────


def _png_bytes(color: tuple[int, int, int] = (255, 0, 0), size: int = 64) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def ensure_pil() -> None:
    pytest.importorskip("PIL.Image")


@pytest.fixture
def mock_capture(monkeypatch: pytest.MonkeyPatch, ensure_pil) -> MagicMock:
    """screen.capture を mock に差し替える。"""
    from addons.vlm_os_agent import screen

    def fake_capture(bbox=None, monitor_index: int = 1):
        return screen.CapturedImage(
            png_bytes=_png_bytes((100, 100, 100)),
            bbox=(0, 0, 64, 64),
            viewport=(64, 64),
        )

    m = MagicMock(side_effect=fake_capture)
    monkeypatch.setattr(screen, "capture", m)
    # agent_runtime 側の import 済みバインディングも上書き
    from addons.vlm_os_agent import agent_runtime

    monkeypatch.setattr(agent_runtime, "capture", m)
    return m


@pytest.fixture
def mock_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """window_focus を no-op 化。find_window は None 返し＝全画面扱い。"""
    from addons.vlm_os_agent import agent_runtime, window_focus

    monkeypatch.setattr(window_focus, "find_window", lambda *a, **kw: None)
    monkeypatch.setattr(window_focus, "focus", lambda info: True)
    monkeypatch.setattr(window_focus, "get_bbox", lambda info: None)
    monkeypatch.setattr(agent_runtime, "find_window", lambda *a, **kw: None)
    monkeypatch.setattr(agent_runtime, "focus", lambda info: True)
    monkeypatch.setattr(agent_runtime, "get_bbox", lambda info: None)


@pytest.fixture
def mock_actuator(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Actuator を丸ごと mock 化。"""
    fake = MagicMock()
    from addons.vlm_os_agent import agent_runtime

    monkeypatch.setattr(agent_runtime, "Actuator", lambda **kw: fake)
    return fake


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    data = {
        "os_find_element": (
            "desc={description} viewport={width}x{height}"
        ),
        "os_find_element_som": "desc={description}",
        "os_agent_plan_step": "goal={goal} window={window_title}",
    }
    (tmp_path / "browser_use_tasks.json").write_text(
        json.dumps(data), encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def mk_runtime(
    mock_capture, mock_window, mock_actuator, templates_dir, tmp_path
):
    """AgentRuntime を組み立てるファクトリ。"""
    from addons.vlm_os_agent.agent_runtime import AgentRuntime, AgentSettings
    from addons.vlm_os_agent.kill_switch import KillSwitch

    def _build(
        lm_client=None,
        *,
        som_enabled: bool = False,
        cache_ttl: int = 3600,
        max_steps: int = 5,
    ):
        if lm_client is None:
            lm_client = MagicMock()
        settings = AgentSettings(
            target_window="",
            poll_ms=10,
            cache_ttl=cache_ttl,
            som_enabled=som_enabled,
            max_steps=max_steps,
            failsafe=False,
            perceive_backend="none",
        )
        addon_dir = tmp_path / "addon"
        addon_dir.mkdir()
        return AgentRuntime(
            lm_client=lm_client,
            configs_dir=templates_dir,
            addon_dir=addon_dir,
            kill_switch=KillSwitch(),
            settings=settings,
        )

    return _build


# ──────────────────────────────────────
# tests
# ──────────────────────────────────────


class TestScreenshot:
    def test_returns_frame(self, mk_runtime) -> None:
        rt = mk_runtime()
        frame = rt.screenshot()
        assert frame.viewport == (64, 64)
        assert frame.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        assert isinstance(frame.phash, str) and len(frame.phash) > 0
        assert frame.image_b64  # base64 エンコード済み


class TestFindElement:
    def test_vlm_returns_valid_coords(self, mk_runtime) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ('{"px_x": 10, "px_y": 20}', None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        r = rt.find_element("send button")
        assert r is not None
        # bbox 左上原点 (0,0) なので絶対座標 = (10, 20)
        assert r.px_x == 10
        assert r.px_y == 20
        assert r.cache_hit is False

    def test_vlm_returns_out_of_viewport(self, mk_runtime) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ('{"px_x": 9999, "px_y": 9999}', None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        assert rt.find_element("x") is None

    def test_vlm_returns_invalid_json(self, mk_runtime) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ("no json here", None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        assert rt.find_element("x") is None

    def test_cache_hit(self, mk_runtime) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ('{"px_x": 5, "px_y": 7}', None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        # 初回 VLM で put
        r1 = rt.find_element("btn", use_cache=True)
        assert r1 is not None

        # 2 回目は cache_hit
        r2 = rt.find_element("btn", use_cache=True)
        assert r2 is not None
        assert r2.cache_hit is True

    def test_sentinel_minus_one(self, mk_runtime) -> None:
        """(-1, -1) は「見つからない」として None にする。"""
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ('{"px_x": -1, "px_y": -1}', None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        assert rt.find_element("x") is None


class TestClick:
    def test_click_flow(self, mk_runtime, mock_actuator) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ('{"px_x": 15, "px_y": 25}', None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)

        r = rt.click("send")
        assert r["ok"] is True
        assert r["px_x"] == 15
        assert r["px_y"] == 25
        mock_actuator.click.assert_called()

    def test_click_element_not_found(self, mk_runtime, mock_actuator) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ("not json", None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        r = rt.click("nothing")
        assert r["ok"] is False
        assert r["reason"] == "element_not_found"
        mock_actuator.click.assert_not_called()


class TestTypeText:
    def test_clipboard_path(self, mk_runtime, mock_actuator) -> None:
        rt = mk_runtime()
        r = rt.type_text("こんにちは", use_clipboard=True, submit=True)
        assert r["ok"] is True
        assert r["submitted"] is True
        mock_actuator.type_clipboard.assert_called_once_with(
            "こんにちは", submit=True,
        )

    def test_ascii_path(self, mk_runtime, mock_actuator) -> None:
        rt = mk_runtime()
        r = rt.type_text("hello", use_clipboard=False)
        assert r["ok"] is True
        mock_actuator.type_ascii.assert_called_once()


class TestRunTask:
    def test_task_done_immediately(self, mk_runtime) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return (
                "",
                [{
                    "function": {
                        "name": "task_done",
                        "arguments": '{"success": true, "reason": "ok"}',
                    }
                }],
            )

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        r = rt.run_task("do the thing")
        assert r["ok"] is True
        assert r["success"] is True

    def test_cancelled_by_esc(self, mk_runtime) -> None:
        """VLM 呼び出し中に ESC が押下された場合を再現。"""
        rt_container: dict = {}

        async def fake_generate(**kwargs):
            # run_task は開始時に reset() するので、LM 呼び出し中に set する
            rt_container["rt"]._ks.set("esc")
            return (
                "",
                [{
                    "function": {
                        "name": "os_find_element",
                        "arguments": '{"description": "btn"}',
                    }
                }],
            )

        lm = MagicMock()
        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        rt_container["rt"] = rt
        r = rt.run_task("x")
        assert r.get("cancelled") is True
        assert r.get("reason") == "esc"

    def test_no_tool_call_ends(self, mk_runtime) -> None:
        lm = MagicMock()

        async def fake_generate(**kwargs):
            return ("thinking...", None)

        lm.generate_with_tools = fake_generate
        rt = mk_runtime(lm_client=lm)
        r = rt.run_task("x")
        assert r["ok"] is True
        # ツール呼び出しなし = 1 ステップで終了
        assert len(r["steps"]) == 1


# ──────────────────────────────────────
# sys.modules cleanup — 他テストから pyautogui/pyperclip/mss が干渉しないよう
# agent_runtime テストでは現実のライブラリは import しない
# ──────────────────────────────────────


@pytest.fixture(autouse=True)
def stub_external_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """pyautogui / pyperclip / mss / pynput が無い環境でも動くよう sentinel を入れる。"""
    for name in ("pyautogui", "pyperclip"):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            if name == "pyautogui":
                stub.FAILSAFE = True  # type: ignore[attr-defined]
                stub.PAUSE = 0.05  # type: ignore[attr-defined]
                stub.moveTo = MagicMock()  # type: ignore[attr-defined]
                stub.click = MagicMock()  # type: ignore[attr-defined]
                stub.dragTo = MagicMock()  # type: ignore[attr-defined]
                stub.typewrite = MagicMock()  # type: ignore[attr-defined]
                stub.hotkey = MagicMock()  # type: ignore[attr-defined]
                stub.press = MagicMock()  # type: ignore[attr-defined]
            else:
                stub.copy = MagicMock()  # type: ignore[attr-defined]
                stub.paste = MagicMock()  # type: ignore[attr-defined]
            monkeypatch.setitem(sys.modules, name, stub)
