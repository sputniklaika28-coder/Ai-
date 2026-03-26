"""
test_prompt_manager.py — PromptManager のユニットテスト

テスト対象:
  - load_templates()
  - get_template()
  - update_template()
  - save_templates()
"""

import json
from pathlib import Path

from core.main import PromptManager


class TestPromptManagerLoad:
    def test_loads_templates_from_json(self, prompts_json: Path):
        pm = PromptManager(config_path=str(prompts_json))
        assert "meta_gm_template" in pm.templates

    def test_handles_missing_file(self, tmp_path: Path, capsys):
        pm = PromptManager(config_path=str(tmp_path / "nonexistent.json"))
        captured = capsys.readouterr()
        assert "警告" in captured.out
        assert pm.templates == {}

    def test_templates_have_system_key(self, prompts_json: Path):
        pm = PromptManager(config_path=str(prompts_json))
        tmpl = pm.templates["meta_gm_template"]
        assert "system" in tmpl


class TestPromptManagerGet:
    def test_get_existing_template(self, prompts_json: Path):
        pm = PromptManager(config_path=str(prompts_json))
        tmpl = pm.get_template("meta_gm_template")
        assert tmpl is not None
        assert tmpl["system"] == "あなたはGMです。"

    def test_get_nonexistent_returns_none(self, prompts_json: Path):
        pm = PromptManager(config_path=str(prompts_json))
        assert pm.get_template("does_not_exist") is None


class TestPromptManagerUpdate:
    def test_update_and_persist(self, prompts_json: Path):
        pm = PromptManager(config_path=str(prompts_json))
        new_data = {"system": "新しいシステムプロンプト", "user_template": "{q}"}
        pm.update_template("new_template", new_data)

        # メモリ上で確認
        assert pm.templates["new_template"]["system"] == "新しいシステムプロンプト"

        # ファイルに永続化されているか確認
        saved = json.loads(prompts_json.read_text(encoding="utf-8"))
        assert "new_template" in saved["templates"]

    def test_overwrite_existing_template(self, prompts_json: Path):
        pm = PromptManager(config_path=str(prompts_json))
        pm.update_template("meta_gm_template", {"system": "上書き済み", "user_template": ""})
        assert pm.templates["meta_gm_template"]["system"] == "上書き済み"

    def test_save_preserves_other_templates(self, prompts_json: Path):
        pm = PromptManager(config_path=str(prompts_json))
        pm.update_template("extra", {"system": "extra", "user_template": ""})
        saved = json.loads(prompts_json.read_text(encoding="utf-8"))
        # 既存テンプレートが消えていないか
        assert "meta_gm_template" in saved["templates"]
        assert "extra" in saved["templates"]
