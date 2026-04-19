"""test_session_orchestrator.py — SessionOrchestrator のユニットテスト。

検証ポイント:
  - セッション無し（session_manager=None）でも基底設定の読み書きができる
  - prepare → start → end のライフサイクルで session_config.json が保存される
  - house_rules / mini_games がセッション固有 > 基底の優先順位でマージされる
  - PCスキル / ステータス / 進行 / シナリオ概要 / あらすじ が統合される
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.session_config import (
    HouseRule,
    MiniGame,
    PCSkill,
    ScenarioProgress,
    SessionConfig,
    SessionStatus,
    merge_mini_games,
    merge_rules,
)
from core.session_manager import SessionManager
from core.session_orchestrator import SESSION_CONFIG_FILENAME, SessionOrchestrator


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "configs").mkdir()
    return tmp_path


@pytest.fixture
def orch_no_session(tmp_root: Path) -> SessionOrchestrator:
    """セッション無しモード（SessionManager=None）の Orchestrator。"""
    return SessionOrchestrator(base_dir=tmp_root, session_manager=None)


@pytest.fixture
def orch_with_session(tmp_root: Path) -> SessionOrchestrator:
    sm = SessionManager(base_dir=tmp_root)
    return SessionOrchestrator(base_dir=tmp_root, session_manager=sm)


# ──────────────────────────────────────────
# セッション無しでの動作
# ──────────────────────────────────────────


class TestNoSessionMode:
    def test_load_returns_default_when_file_empty(self, tmp_root: Path):
        (tmp_root / "configs" / SESSION_CONFIG_FILENAME).write_text("")
        orch = SessionOrchestrator(base_dir=tmp_root)
        assert orch.base_config.scenario_overview == ""

    def test_load_returns_default_when_missing(self, orch_no_session: SessionOrchestrator):
        assert orch_no_session.base_config.session_name == ""
        assert orch_no_session.is_session_active is False

    def test_save_and_reload_base(self, tmp_root: Path):
        orch = SessionOrchestrator(base_dir=tmp_root)
        orch.base_config.scenario_overview = "古城の謎"
        orch.base_config.house_rules.append(HouseRule(name="致命傷ルール", priority=50))
        orch.save_base_config()

        orch2 = SessionOrchestrator(base_dir=tmp_root)
        assert orch2.base_config.scenario_overview == "古城の謎"
        assert orch2.base_config.house_rules[0].name == "致命傷ルール"

    def test_get_effective_returns_base_copy_when_no_session(
        self, orch_no_session: SessionOrchestrator
    ):
        orch_no_session.base_config.scenario_synopsis = "プレイヤーは森で目覚める。"
        eff = orch_no_session.get_effective_config()
        assert eff.scenario_synopsis == "プレイヤーは森で目覚める。"
        assert orch_no_session.is_session_active is False

    def test_start_without_session_manager_runs_lightweight(
        self, orch_no_session: SessionOrchestrator
    ):
        folder = orch_no_session.start_session("Light")
        assert folder is None
        assert orch_no_session.is_session_active is True
        assert orch_no_session.active_config.session_name == "Light"

    def test_add_house_rule_without_active_creates_session_scope(
        self, orch_no_session: SessionOrchestrator
    ):
        orch_no_session.add_house_rule(HouseRule(name="即死回避", priority=10))
        assert orch_no_session.active_config is not None
        assert orch_no_session.active_config.house_rules[0].name == "即死回避"


# ──────────────────────────────────────────
# ライフサイクル（SessionManager 連携）
# ──────────────────────────────────────────


class TestLifecycle:
    def test_prepare_then_start_writes_session_config(
        self, orch_with_session: SessionOrchestrator
    ):
        cfg = SessionConfig(
            session_name="第1話_嵐の村",
            scenario_overview="魔女の呪いを解け",
            scenario_synopsis="夜更けの村に集まる4人のPC。",
            pc_skills=[PCSkill(character="リン", name="目星", value=70)],
        )
        orch_with_session.prepare_session(cfg)
        folder = orch_with_session.start_session()
        assert folder is not None and "第1話" in folder

        sm = orch_with_session.session_manager
        out = sm.current_session_dir / SESSION_CONFIG_FILENAME
        assert out.exists()
        saved = json.loads(out.read_text(encoding="utf-8"))
        assert saved["scenario_overview"] == "魔女の呪いを解け"
        assert saved["pc_skills"][0]["name"] == "目星"
        assert saved["history_ref"] == folder

    def test_prepare_accepts_dict(self, orch_with_session: SessionOrchestrator):
        cfg = orch_with_session.prepare_session(
            {"session_name": "DictSess", "scenario_synopsis": "短い前振り"}
        )
        assert cfg.session_name == "DictSess"
        assert cfg.scenario_synopsis == "短い前振り"

    def test_end_session_clears_active_and_persists(
        self, orch_with_session: SessionOrchestrator
    ):
        orch_with_session.prepare_session(SessionConfig(session_name="Endable"))
        orch_with_session.start_session()
        sm = orch_with_session.session_manager
        session_dir = sm.current_session_dir
        orch_with_session.end_session()
        assert orch_with_session.active_config is None
        # 設定ファイルは残る
        assert (session_dir / SESSION_CONFIG_FILENAME).exists()

    def test_start_overrides_session_name(self, orch_with_session: SessionOrchestrator):
        orch_with_session.prepare_session(SessionConfig(session_name="Old"))
        folder = orch_with_session.start_session("New")
        assert "New" in folder
        assert orch_with_session.active_config.session_name == "New"


# ──────────────────────────────────────────
# ハウスルール / ミニゲームの優先順位
# ──────────────────────────────────────────


class TestPriority:
    def test_session_house_rule_overrides_base_by_name(
        self, orch_with_session: SessionOrchestrator
    ):
        orch_with_session.base_config.house_rules.append(
            HouseRule(name="致命傷", description="基底版", priority=50)
        )
        orch_with_session.prepare_session(SessionConfig(session_name="X"))
        orch_with_session.add_house_rule(
            HouseRule(name="致命傷", description="セッション版", priority=10)
        )
        eff = orch_with_session.get_effective_config()
        rule = next(r for r in eff.house_rules if r.name == "致命傷")
        assert rule.description == "セッション版"
        # priority に下駄を履いている
        assert rule.priority >= 1000

    def test_active_rules_sorted_by_priority(
        self, orch_with_session: SessionOrchestrator
    ):
        orch_with_session.base_config.house_rules.extend(
            [
                HouseRule(name="A", priority=10),
                HouseRule(name="B", priority=200),
            ]
        )
        rules = orch_with_session.list_active_house_rules()
        assert [r.name for r in rules] == ["B", "A"]

    def test_disabled_rule_excluded_from_active(
        self, orch_with_session: SessionOrchestrator
    ):
        orch_with_session.add_house_rule(HouseRule(name="無効", enabled=False))
        names = [r.name for r in orch_with_session.list_active_house_rules()]
        assert "無効" not in names

    def test_session_mini_game_overrides_base(
        self, orch_with_session: SessionOrchestrator
    ):
        orch_with_session.add_mini_game(
            MiniGame(name="銃撃戦", description="基底", priority=1), scope="base"
        )
        orch_with_session.add_mini_game(
            MiniGame(name="銃撃戦", description="卓固有", priority=1), scope="session"
        )
        games = orch_with_session.list_active_mini_games()
        assert games[0].description == "卓固有"

    def test_merge_rules_unit(self):
        base = [HouseRule(name="X", priority=100)]
        over = [HouseRule(name="X", priority=5)]
        merged = merge_rules(base, over)
        assert merged[0].priority == 1005

    def test_merge_mini_games_unit(self):
        base = [MiniGame(name="dice", priority=1)]
        over = [MiniGame(name="dice", priority=2)]
        assert merge_mini_games(base, over)[0].priority == 1002


# ──────────────────────────────────────────
# 統合ビュー
# ──────────────────────────────────────────


class TestEffectiveConfigMerge:
    def test_active_synopsis_overrides_base(self, orch_with_session: SessionOrchestrator):
        orch_with_session.base_config.scenario_synopsis = "基底のあらすじ"
        orch_with_session.prepare_session(SessionConfig(scenario_synopsis="今夜のあらすじ"))
        eff = orch_with_session.get_effective_config()
        assert eff.scenario_synopsis == "今夜のあらすじ"

    def test_status_and_progress_taken_from_active(
        self, orch_with_session: SessionOrchestrator
    ):
        orch_with_session.prepare_session(
            SessionConfig(
                status=SessionStatus(scene="洞窟", turn=3, phase="戦闘"),
                progress=ScenarioProgress(
                    current_chapter="第3章",
                    completed_scenes=["序章", "第1章"],
                    flags={"鍵入手": True},
                ),
            )
        )
        eff = orch_with_session.get_effective_config()
        assert eff.status.scene == "洞窟"
        assert eff.status.turn == 3
        assert eff.progress.current_chapter == "第3章"
        assert eff.progress.flags["鍵入手"] is True

    def test_settings_dict_merged(self, orch_with_session: SessionOrchestrator):
        orch_with_session.base_config.settings = {"difficulty": "normal", "lang": "ja"}
        orch_with_session.prepare_session(
            SessionConfig(settings={"difficulty": "hard"})
        )
        eff = orch_with_session.get_effective_config()
        assert eff.settings == {"difficulty": "hard", "lang": "ja"}
