"""
test_session_manager.py — SessionManager のユニットテスト

テスト対象:
  - start_new_session()
  - log_message()
  - load_session()
"""

import json
from pathlib import Path

import pytest

from core.session_manager import SessionManager


@pytest.fixture
def sm(tmp_path: Path) -> SessionManager:
    """一時ディレクトリを使った SessionManager インスタンス"""
    # configs_backup のコピー元として空の configs フォルダを作る
    (tmp_path / "configs").mkdir()
    return SessionManager(base_dir=tmp_path)


class TestStartNewSession:
    def test_creates_session_directory(self, sm: SessionManager):
        sm.start_new_session("TestSession")
        assert sm.current_session_dir is not None
        assert sm.current_session_dir.exists()

    def test_creates_log_file(self, sm: SessionManager):
        sm.start_new_session("LogTest")
        assert sm.log_file is not None
        assert sm.log_file.exists()
        assert sm.log_file.suffix == ".jsonl"

    def test_directory_name_contains_session_name(self, sm: SessionManager):
        sm.start_new_session("BattleScene")
        assert "BattleScene" in sm.current_session_dir.name

    def test_history_reset_on_new_session(self, sm: SessionManager):
        sm.start_new_session("First")
        sm.log_message("GM", "開始します")
        sm.start_new_session("Second")
        assert sm.history == []

    def test_configs_backup_created(self, sm: SessionManager, tmp_path: Path):
        # configs フォルダにダミーファイルを置く
        (tmp_path / "configs" / "test.json").write_text("{}", encoding="utf-8")
        sm.start_new_session("BackupTest")
        backup_dir = sm.current_session_dir / "configs_backup"
        assert backup_dir.exists()
        assert (backup_dir / "test.json").exists()


class TestLogMessage:
    def test_appends_to_jsonl_file(self, sm: SessionManager):
        sm.start_new_session("LogTest")
        sm.log_message("GM", "シーン開始")
        sm.log_message("Player", "移動します")

        lines = sm.log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_log_entry_has_required_fields(self, sm: SessionManager):
        sm.start_new_session("FieldTest")
        sm.log_message("AI", "攻撃します")

        entry = json.loads(sm.log_file.read_text(encoding="utf-8").strip())
        assert "timestamp" in entry
        assert entry["speaker"] == "AI"
        assert entry["body"] == "攻撃します"

    def test_appended_to_history(self, sm: SessionManager):
        sm.start_new_session("HistTest")
        sm.log_message("GM", "msg1")
        sm.log_message("Player", "msg2")
        assert len(sm.history) == 2
        assert sm.history[0]["speaker"] == "GM"

    def test_no_crash_before_start(self, sm: SessionManager):
        """start_new_session を呼ばずに log_message しても例外を出さない"""
        sm.log_message("GM", "should not crash")


class TestLoadSession:
    def test_load_existing_session(self, sm: SessionManager):
        sm.start_new_session("Reload")
        sm.log_message("GM", "ロードテスト")
        folder_name = sm.current_session_dir.name

        sm2 = SessionManager(base_dir=sm.base_dir)
        result = sm2.load_session(folder_name)
        assert result is True

    def test_load_restores_history(self, sm: SessionManager):
        sm.start_new_session("Restore")
        sm.log_message("GM", "行動1")
        sm.log_message("Player", "行動2")
        folder_name = sm.current_session_dir.name

        sm2 = SessionManager(base_dir=sm.base_dir)
        sm2.load_session(folder_name)
        assert len(sm2.history) == 2

    def test_load_nonexistent_returns_false(self, sm: SessionManager, capsys):
        result = sm.load_session("ghost_session")
        assert result is False
