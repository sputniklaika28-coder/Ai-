import json
from pathlib import Path


class CharacterManager:
    """キャラクター管理クラス（最大15キャラ対応）"""

    def __init__(self, config_path: str = "configs/characters.json"):
        self.config_path = Path(config_path)
        self.characters: dict[str, dict] = {}
        self.load_characters()

    def load_characters(self):
        """JSONからキャラクターを読み込み"""
        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
                self.characters = data.get("characters", {})

    def get_character(self, character_id: str) -> dict | None:
        """IDからキャラクターを取得"""
        return self.characters.get(character_id)

    def get_enabled_characters(self) -> list[dict]:
        """有効なキャラクター一覧を取得"""
        return [char for char in self.characters.values() if char.get("enabled", False)]

    def get_character_count(self) -> int:
        """現在のキャラクター数"""
        return len(self.characters)

    def upsert_character(self, entry: dict) -> None:
        """characters.json にエントリを追加または更新する。

        entry["id"] が無い場合は entry["name"] からスラッグを生成する。
        ファイルへの書き込みも同時に行い、メモリ状態も更新する。
        """
        char_id = entry.get("id") or _slugify(entry.get("name", ""))
        if not char_id:
            raise ValueError("upsert_character: id と name のどちらも空です")
        entry = dict(entry)
        entry["id"] = char_id
        existing = self.characters.get(char_id, {})
        merged = {**existing, **entry}
        self.characters[char_id] = merged
        self._write()

    def link_sheet(self, char_id: str, sheet_file: str) -> None:
        """キャラクターエントリにシートファイルパスをリンクする。"""
        if char_id not in self.characters:
            raise KeyError(f"キャラクター {char_id} が存在しません")
        self.characters[char_id]["sheet_file"] = sheet_file
        self._write()

    def _write(self) -> None:
        """現在の characters を JSON に書き出す。"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps({"characters": self.characters}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _slugify(name: str) -> str:
    """キャラクター名から英数字/アンダースコアのみの ID を生成する。

    日本語名はハッシュの短縮版を使う。
    """
    import hashlib
    import re as _re

    safe = _re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")
    if safe:
        return safe
    if not name:
        return ""
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"pc_{digest}"
