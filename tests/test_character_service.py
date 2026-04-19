"""test_character_service.py — CharacterService のユニットテスト。

CharacterService は:
  - save_bundle: saved_pcs/<name>.json と characters.json を同時更新する
  - load_bundle: 保存済みシートを復元する
  - to_vtt_clipboard: active RuleSystemAddon.build_vtt_piece_data() の JSON を返す
  - generate_from_concept: AI 構造化生成 (LMClient 依存、ここでは触れない)

tactical_exorcist アドオンが active のときに:
  - status[] に 10 項目 (体力/霊力/回避D + 支給装備 7 種)
  - params[] に 6 項目 (体/霊/巧/術/機動力/装甲)
  - commands に支給装備 4 種 (形代/祓串/注連鋼縄/呪瘤檀) の説明が含まれる
ことを確認する。

ルールシステム未設定 (デフォルト) 時は最小限のペイロードが返ることも確認する。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.character_manager import CharacterManager
from core.character_service import CharacterBundle, CharacterService


# ──────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture
def saved_pcs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "saved_pcs"
    d.mkdir()
    return d


@pytest.fixture
def characters_file(tmp_path: Path) -> Path:
    p = tmp_path / "characters.json"
    p.write_text(json.dumps({"characters": {}}, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def service_no_addon(saved_pcs_dir: Path, characters_file: Path) -> CharacterService:
    cm = CharacterManager(config_path=str(characters_file))
    return CharacterService(
        saved_pcs_dir=saved_pcs_dir,
        characters_file=characters_file,
        character_manager=cm,
        addon_manager=None,
        lm_client=None,
    )


@pytest.fixture
def service_with_tactical_exorcist(
    saved_pcs_dir: Path, characters_file: Path
) -> CharacterService:
    """TacticalExorcistAddon を active にした CharacterService。"""
    from addons.tactical_exorcist.addon import TacticalExorcistAddon

    addon = TacticalExorcistAddon()
    # manifest / addon_dir の最小セット
    addon.manifest = MagicMock()
    addon.manifest.id = "tactical_exorcist"
    addon.addon_dir = Path(__file__).parent.parent / "addons" / "tactical_exorcist"
    # on_load で prompts/world_setting の読み込みを行うが、無くても動く
    ctx = MagicMock()
    ctx.lm_client = MagicMock()
    addon.on_load(ctx)

    mock_mgr = MagicMock()
    mock_mgr.get_active_rule_system.return_value = addon

    cm = CharacterManager(config_path=str(characters_file))
    return CharacterService(
        saved_pcs_dir=saved_pcs_dir,
        characters_file=characters_file,
        character_manager=cm,
        addon_manager=mock_mgr,
        lm_client=None,
    )


# ──────────────────────────────────────────
# save_bundle / load_bundle
# ──────────────────────────────────────────


class TestSaveBundle:
    def test_writes_both_sheet_and_roster(
        self, service_no_addon: CharacterService, saved_pcs_dir: Path, characters_file: Path
    ) -> None:
        bundle = CharacterBundle(
            sheet={"name": "テスト太郎", "hp": 15, "memo": "背景"},
            roster_entry={"name": "テスト太郎"},
        )
        path = service_no_addon.save_bundle(bundle)

        assert path.exists()
        assert path.parent == saved_pcs_dir

        roster = json.loads(characters_file.read_text(encoding="utf-8"))
        # id はスラッグ化されるが name で検索する
        names = [c.get("name") for c in roster["characters"].values()]
        assert "テスト太郎" in names

    def test_rejects_empty_name(self, service_no_addon: CharacterService) -> None:
        bundle = CharacterBundle(sheet={"name": ""}, roster_entry={})
        with pytest.raises(ValueError):
            service_no_addon.save_bundle(bundle)

    def test_sets_is_ai_flag(
        self, service_no_addon: CharacterService, characters_file: Path
    ) -> None:
        bundle = CharacterBundle(
            sheet={"name": "AI子"},
            roster_entry={"name": "AI子"},
        )
        service_no_addon.save_bundle(bundle, is_ai=True)
        roster = json.loads(characters_file.read_text(encoding="utf-8"))
        entry = next(
            c for c in roster["characters"].values() if c.get("name") == "AI子"
        )
        assert entry["is_ai"] is True

    def test_load_bundle_roundtrip(self, service_no_addon: CharacterService) -> None:
        original = CharacterBundle(
            sheet={"name": "往復太郎", "hp": 20, "memo": "復元テスト"},
            roster_entry={"name": "往復太郎"},
        )
        service_no_addon.save_bundle(original)

        loaded = service_no_addon.load_bundle("往復太郎")
        assert loaded is not None
        assert loaded.sheet["name"] == "往復太郎"
        assert loaded.sheet["hp"] == 20


# ──────────────────────────────────────────
# to_vtt_clipboard (fallback — no rule system)
# ──────────────────────────────────────────


class TestVTTFallback:
    def test_returns_minimal_payload(self, service_no_addon: CharacterService) -> None:
        """アドオン未指定時は最低限のペイロードを返す。"""
        payload = json.loads(service_no_addon.to_vtt_clipboard({"name": "汎用"}))
        assert payload["kind"] == "character"
        assert payload["data"]["name"] == "汎用"
        # 最低限のフィールドがすべて揃う
        assert "status" in payload["data"]
        assert "params" in payload["data"]
        assert "commands" in payload["data"]


# ──────────────────────────────────────────
# to_vtt_clipboard (tactical_exorcist 固有)
# ──────────────────────────────────────────


class TestVTTTacticalExorcist:
    def test_has_ten_status_items(
        self, service_with_tactical_exorcist: CharacterService
    ) -> None:
        sheet = {
            "name": "祓魔師テスト",
            "hp": 20, "sp": 18, "evasion": 3,
            "mobility": 3, "armor": 1,
            "body": 3, "soul": 4, "skill": 3, "magic": 3,
            "items": {
                "katashiro": 2, "haraegushi": 1, "shimenawa": 1,
                "juryudan": 0, "ireikigu": 1, "meifuku": 0, "jutsuyen": 0,
            },
        }
        payload = json.loads(service_with_tactical_exorcist.to_vtt_clipboard(sheet))
        status = payload["data"]["status"]
        assert len(status) == 10
        labels = [s["label"] for s in status]
        assert labels == [
            "体力", "霊力", "回避D",
            "形代", "祓串", "注連鋼縄", "呪瘤檀",
            "医霊器具", "名伏", "術延起点",
        ]

    def test_has_six_params_with_mobility_and_armor(
        self, service_with_tactical_exorcist: CharacterService
    ) -> None:
        sheet = {
            "name": "祓魔師テスト",
            "hp": 15, "sp": 15, "evasion": 2,
            "mobility": 4, "armor": 2,
            "body": 3, "soul": 3, "skill": 3, "magic": 3,
        }
        payload = json.loads(service_with_tactical_exorcist.to_vtt_clipboard(sheet))
        params = payload["data"]["params"]
        labels = [p["label"] for p in params]
        assert labels == ["体", "霊", "巧", "術", "機動力", "装甲"]
        # 機動力 / 装甲 が欠落していない
        mobility = next(p for p in params if p["label"] == "機動力")
        armor = next(p for p in params if p["label"] == "装甲")
        assert mobility["value"] == "4"
        assert armor["value"] == "2"

    def test_commands_include_four_equipment_descriptions(
        self, service_with_tactical_exorcist: CharacterService
    ) -> None:
        """支給装備 4 種 (形代/祓串/注連鋼縄/呪瘤檀) のチャットパレット説明文が含まれる。"""
        sheet = {"name": "祓魔師テスト"}
        payload = json.loads(service_with_tactical_exorcist.to_vtt_clipboard(sheet))
        commands = payload["data"]["commands"]
        assert "【形代】" in commands
        assert "【祓串】" in commands
        assert "【注連鋼縄】" in commands
        assert "【呪瘤檀】" in commands
        # 能力値判定とダメージロールも含まれる
        assert "◆能力値を使った判定◆" in commands
        assert "2d6" in commands

    def test_skills_and_weapons_included_in_commands(
        self, service_with_tactical_exorcist: CharacterService
    ) -> None:
        sheet = {
            "name": "武装テスト",
            "skills": [{"name": "戦術機動", "description": "移動強化"}],
            "weapons": [{"name": "大型遠隔祭具", "description": "遠隔5点"}],
        }
        payload = json.loads(service_with_tactical_exorcist.to_vtt_clipboard(sheet))
        commands = payload["data"]["commands"]
        assert "【戦術機動】" in commands
        assert "移動強化" in commands
        assert "【大型遠隔祭具】" in commands


# ──────────────────────────────────────────
# list_saved / delete
# ──────────────────────────────────────────


class TestListAndDelete:
    def test_list_saved_returns_names(
        self, service_no_addon: CharacterService
    ) -> None:
        service_no_addon.save_bundle(
            CharacterBundle(sheet={"name": "A"}, roster_entry={"name": "A"})
        )
        service_no_addon.save_bundle(
            CharacterBundle(sheet={"name": "B"}, roster_entry={"name": "B"})
        )
        names = service_no_addon.list_saved()
        assert "A" in names
        assert "B" in names

    def test_delete_removes_file(
        self, service_no_addon: CharacterService, saved_pcs_dir: Path
    ) -> None:
        service_no_addon.save_bundle(
            CharacterBundle(sheet={"name": "削除対象"}, roster_entry={"name": "削除対象"})
        )
        assert service_no_addon.delete("削除対象") is True
        assert not (saved_pcs_dir / "削除対象.json").exists()

    def test_delete_missing_returns_false(
        self, service_no_addon: CharacterService
    ) -> None:
        assert service_no_addon.delete("存在しない") is False
