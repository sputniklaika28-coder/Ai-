import json
from pathlib import Path


class PromptManager:
    """プロンプト管理クラス"""

    def __init__(self, config_path: str = "configs/prompts.json"):
        self.config_path = Path(config_path)
        self.templates: dict[str, dict] = {}
        self.load_templates()

    def load_templates(self):
        """JSONからプロンプトテンプレートを読み込み"""
        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
                self.templates = data.get("templates", {})
        else:
            print(f"警告: {self.config_path} が見つかりません")

    def get_template(self, template_id: str) -> dict | None:
        """テンプレートIDからプロンプトテンプレートを取得"""
        return self.templates.get(template_id)

    def update_template(self, template_id: str, template_data: dict):
        """テンプレートを更新"""
        self.templates[template_id] = template_data
        self.save_templates()

    def save_templates(self):
        """テンプレートをJSONに保存"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"templates": self.templates}, f, ensure_ascii=False, indent=2)
