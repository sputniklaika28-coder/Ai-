"""
test_ccfolia_connector.py — CCFoliaConnector のユニットテスト

BuildModeStatus, SystemHealthStatus, CDP接続対応,
ビルドモード制御, ヘルスツールのテスト。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.ccfolia_connector import (
    BUILD_MODE_TOOLS,
    HEALTH_TOOLS,
    BuildModeStatus,
    SystemHealthStatus,
)


# ──────────────────────────────────────────
# BuildModeStatus
# ──────────────────────────────────────────


class TestBuildModeStatus:
    def test_initial_state(self):
        s = BuildModeStatus()
        assert s.is_active is False
        assert s.current_step == ""
        assert s.completed_steps == 0
        assert s.total_steps == 0
        assert s.errors == []

    def test_reset(self):
        s = BuildModeStatus(
            is_active=True,
            current_step="create_room",
            completed_steps=2,
            total_steps=4,
            errors=["テストエラー"],
        )
        s.reset()
        assert s.is_active is False
        assert s.completed_steps == 0
        assert s.errors == []


# ──────────────────────────────────────────
# SystemHealthStatus
# ──────────────────────────────────────────


class TestSystemHealthStatus:
    def test_initial_state(self):
        h = SystemHealthStatus()
        assert h.vtt_connected is False
        assert h.vtt_mode == "disconnected"
        assert h.lm_reachable is False
        assert h.build_mode == "idle"

    def test_to_display(self):
        h = SystemHealthStatus(
            vtt_connected=True,
            vtt_mode="cdp",
            lm_reachable=True,
            build_mode="idle",
        )
        display = h.to_display()
        assert "○" in display
        assert "cdp" in display

    def test_to_display_disconnected(self):
        h = SystemHealthStatus()
        display = h.to_display()
        assert "×" in display
        assert "disconnected" in display

    def test_to_dict(self):
        h = SystemHealthStatus(
            vtt_connected=True,
            vtt_mode="cdp",
            lm_reachable=False,
            build_mode="building",
            room_url="https://ccfolia.com/rooms/test",
        )
        d = h.to_dict()
        assert d["vtt_connected"] is True
        assert d["vtt_mode"] == "cdp"
        assert d["lm_reachable"] is False
        assert d["build_mode"] == "building"
        assert d["room_url"] == "https://ccfolia.com/rooms/test"


# ──────────────────────────────────────────
# ツール定義の存在確認
# ──────────────────────────────────────────


class TestToolDefinitions:
    def test_health_tools_exist(self):
        names = [t["function"]["name"] for t in HEALTH_TOOLS]
        assert "get_system_health" in names

    def test_build_mode_tools_exist(self):
        names = [t["function"]["name"] for t in BUILD_MODE_TOOLS]
        assert "enter_build_mode" in names
        assert "exit_build_mode" in names


# ──────────────────────────────────────────
# CCFoliaConnector ビルドモード（モック環境）
# ──────────────────────────────────────────


class TestBuildModeIntegration:
    """CCFoliaConnector のビルドモード制御をテストする。

    外部依存（Playwright, LM Studio）はすべてモック化。
    """

    @pytest.fixture()
    def mock_connector(self, tmp_path):
        """最小構成の CCFoliaConnector モック。"""
        # characters.json と prompts.json を作成
        chars_path = tmp_path / "configs" / "characters.json"
        chars_path.parent.mkdir(parents=True)
        chars_path.write_text(
            json.dumps({"characters": {"meta_gm": {
                "id": "meta_gm", "name": "GM", "layer": "meta",
                "role": "game_master", "enabled": True, "is_ai": True,
                "prompt_id": "t1", "keywords": ["GM"],
            }}}, ensure_ascii=False),
            encoding="utf-8",
        )
        prompts_path = tmp_path / "configs" / "prompts.json"
        prompts_path.write_text(
            json.dumps({"templates": {"t1": {"system": "test", "user_template": "{user_input}"}}}),
            encoding="utf-8",
        )
        ws_path = tmp_path / "configs" / "world_setting.json"
        ws_path.write_text("{}", encoding="utf-8")

        # モックを使って CCFoliaConnector を最小限で初期化
        with (
            patch("core.ccfolia_connector.CharacterManager") as MockCM,
            patch("core.ccfolia_connector.PromptManager") as MockPM,
            patch("core.ccfolia_connector.LMClient") as MockLM,
            patch("core.ccfolia_connector.SessionManager") as MockSM,
            patch("core.ccfolia_connector.AddonManager") as MockAddonMgr,
        ):
            MockAddonMgr.return_value.discover.return_value = []
            MockAddonMgr.return_value.get_all_tools.return_value = []
            MockAddonMgr.return_value.get_active_rule_system.return_value = None
            MockAddonMgr.return_value.loaded_addons = {}
            MockCM.return_value.characters = {
                "meta_gm": {
                    "id": "meta_gm", "name": "GM", "enabled": True,
                    "is_ai": True, "keywords": ["GM"],
                }
            }
            MockCM.return_value.get_character.return_value = {
                "id": "meta_gm", "name": "GM",
            }
            MockCM.return_value.get_character_count.return_value = 1
            MockPM.return_value.templates = {"t1": {"system": "test"}}
            MockPM.return_value.get_template.return_value = {"system": "test"}
            MockLM.return_value.generate_response.return_value = ("pong", None)
            MockSM.return_value.configs_dir = tmp_path / "configs"

            from core.ccfolia_connector import CCFoliaConnector
            connector = CCFoliaConnector(
                room_url="https://ccfolia.com/rooms/test",
                default_character_id="meta_gm",
            )
            # モックアダプターを設定
            connector.adapter = MagicMock()
            connector.adapter.send_chat.return_value = True

        return connector

    def test_enter_exit_build_mode(self, mock_connector):
        c = mock_connector
        assert c._build_status.is_active is False
        assert c._health.build_mode == "idle"

        c.enter_build_mode("GM")
        assert c._build_status.is_active is True
        assert c._health.build_mode == "building"

        c.exit_build_mode("GM")
        assert c._build_status.is_active is False
        assert c._health.build_mode == "idle"

    def test_build_mode_blocks_enter_while_active(self, mock_connector):
        """ビルドモード中に再度 enter_build_mode を呼ぶとエラー。"""
        c = mock_connector
        c._build_status.is_active = True

        finished, result_json = c._execute_tool(
            "enter_build_mode", {}, "GM", "tc1",
        )
        result = json.loads(result_json)
        assert "error" in result

    def test_health_tool_returns_status(self, mock_connector):
        c = mock_connector
        c._health.vtt_connected = True
        c._health.vtt_mode = "cdp"

        finished, result_json = c._execute_tool(
            "get_system_health", {}, "GM", "tc1",
        )
        result = json.loads(result_json)
        assert result["vtt_connected"] is True
        assert result["vtt_mode"] == "cdp"

    def test_enter_build_mode_tool(self, mock_connector):
        c = mock_connector
        finished, result_json = c._execute_tool(
            "enter_build_mode", {}, "GM", "tc1",
        )
        result = json.loads(result_json)
        assert result["ok"] is True
        assert c._build_status.is_active is True

    def test_exit_build_mode_tool(self, mock_connector):
        c = mock_connector
        c._build_status.is_active = True
        c._health.build_mode = "building"

        finished, result_json = c._execute_tool(
            "exit_build_mode", {}, "GM", "tc1",
        )
        result = json.loads(result_json)
        assert result["ok"] is True
        assert c._build_status.is_active is False

    def test_check_lm_health(self, mock_connector):
        c = mock_connector
        # LMClient mock returns ("pong", None)
        result = c._check_lm_health()
        assert result is True
        assert c._health.lm_reachable is True


# ──────────────────────────────────────────
# CDP引数パース
# ──────────────────────────────────────────


class TestCDPArgument:
    def test_connector_accepts_cdp_url(self):
        """CCFoliaConnector が cdp_url パラメータを受け取れること。"""
        with (
            patch("core.ccfolia_connector.CharacterManager"),
            patch("core.ccfolia_connector.PromptManager"),
            patch("core.ccfolia_connector.LMClient"),
            patch("core.ccfolia_connector.SessionManager"),
        ):
            from core.ccfolia_connector import CCFoliaConnector
            c = CCFoliaConnector(
                room_url="https://ccfolia.com/rooms/test",
                cdp_url="http://localhost:9222",
            )
            assert c.cdp_url == "http://localhost:9222"

    def test_connector_cdp_url_default_none(self):
        """cdp_url のデフォルト値は None。"""
        with (
            patch("core.ccfolia_connector.CharacterManager"),
            patch("core.ccfolia_connector.PromptManager"),
            patch("core.ccfolia_connector.LMClient"),
            patch("core.ccfolia_connector.SessionManager"),
        ):
            from core.ccfolia_connector import CCFoliaConnector
            c = CCFoliaConnector(room_url="https://ccfolia.com/rooms/test")
            assert c.cdp_url is None


# ──────────────────────────────────────────
# Phase 5: GMDirector 統合テスト
# ──────────────────────────────────────────


class TestGMDirectorIntegration:
    """CCFoliaConnector の GMDirector 直接統合 (Phase 5) テスト。"""

    @pytest.fixture()
    def mock_connector(self, tmp_path):
        """最小構成の CCFoliaConnector モック（TestToolExecution と同一）。"""
        with (
            patch("core.ccfolia_connector.CharacterManager") as MockCM,
            patch("core.ccfolia_connector.PromptManager") as MockPM,
            patch("core.ccfolia_connector.LMClient") as MockLM,
            patch("core.ccfolia_connector.SessionManager") as MockSM,
            patch("core.ccfolia_connector.AddonManager") as MockAddonMgr,
        ):
            MockAddonMgr.return_value.discover.return_value = []
            MockAddonMgr.return_value.get_all_tools.return_value = []
            MockAddonMgr.return_value.get_active_rule_system.return_value = None
            MockAddonMgr.return_value.loaded_addons = {}
            MockAddonMgr.return_value.get_addon.side_effect = Exception("addon not found")
            MockCM.return_value.get_character.return_value = {"id": "meta_gm", "name": "GM"}
            MockCM.return_value.get_character_count.return_value = 1
            MockPM.return_value.templates = {}
            MockPM.return_value.get_template.return_value = {"system": "test"}
            MockLM.return_value.generate_response.return_value = ("pong", None)
            MockSM.return_value.configs_dir = tmp_path / "configs"
            (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
            (tmp_path / "configs" / "world_setting.json").write_text("{}", encoding="utf-8")

            from core.ccfolia_connector import CCFoliaConnector
            connector = CCFoliaConnector(
                room_url="https://ccfolia.com/rooms/test",
                default_character_id="meta_gm",
            )
            connector.adapter = MagicMock()
            connector.adapter.send_chat.return_value = True
        return connector

    def test_init_creates_instance(self, mock_connector):
        """_init_gm_director() 後に _gm_director が None でないこと。"""
        c = mock_connector
        c._init_gm_director()
        assert c._gm_director is not None

    def test_shares_memory_with_session_context(self, mock_connector):
        """_gm_director._memory が SessionContext 内部の MemoryManager と同一であること。"""
        c = mock_connector
        c._init_gm_director()
        assert c._gm_director._memory is c.ctx._memory

    def test_entity_tracker_created(self, mock_connector):
        """_init_gm_director() 後に _entity_tracker が作成されること。"""
        c = mock_connector
        c._init_gm_director()
        assert c._entity_tracker is not None

    def test_gm_director_none_before_init(self, mock_connector):
        """初期化前は _gm_director が None であること。"""
        c = mock_connector
        assert c._gm_director is None

    def test_fallback_called_when_gm_director_none(self, mock_connector):
        """_gm_director が None の場合、_fallback_simple_response が利用可能なこと。"""
        c = mock_connector
        c._gm_director = None
        # _fallback_simple_response メソッドが存在し呼び出し可能であること
        assert hasattr(c, '_fallback_simple_response')
        assert callable(c._fallback_simple_response)

    def test_gm_director_process_turn_called_on_non_combat(self, mock_connector):
        """非戦闘フェーズで process_turn が呼ばれ VTT に投稿されること。"""
        import asyncio
        from unittest.mock import AsyncMock
        from core.gm_director import GMTurnResult
        from core.schemas import GameIntention

        c = mock_connector
        c._init_gm_director()

        mock_result = GMTurnResult(
            intention=GameIntention(actor="テスト", action_type="other"),
            combat_result=None,
            narration="テストナレーション",
            vtt_chat_lines=["テストナレーション"],
        )
        c._gm_director.process_turn = AsyncMock(return_value=mock_result)
        c.ctx.phase = "free"

        loop = asyncio.new_event_loop()
        c._async_loop = loop
        import threading
        threading.Thread(target=loop.run_forever, daemon=True).start()

        target_char = {"name": "GM", "prompt_id": "t1"}
        body = "探索する＞"
        speaker = "アリス"

        import asyncio as _asyncio
        future = _asyncio.run_coroutine_threadsafe(
            c._gm_director.process_turn(body, speaker, "【フェイズ: FREE】"),
            loop,
        )
        result = future.result(timeout=5)
        assert result.narration == "テストナレーション"
