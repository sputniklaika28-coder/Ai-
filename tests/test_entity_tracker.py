"""tests/test_entity_tracker.py — EntityTracker のユニットテスト。"""

from __future__ import annotations

import pytest

from core.entity_tracker import Entity, EntityTracker


# ──────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────


def _tracker_with_data() -> EntityTracker:
    t = EntityTracker()
    t.upsert("山田神主", "npc", {"disposition": "友好的"}, notes="神社の神主")
    t.upsert("鈴木刑事", "npc", {"disposition": "中立"}, notes="警察関係者")
    t.upsert("聖剣カグツチ", "item", {"location": "アリスの所持品"})
    t.upsert("封印の神社", "location", {"region": "東京"})
    t.upsert("封印解除", "quest_flag", {"completed": False})
    return t


# ──────────────────────────────────────
# upsert() のテスト
# ──────────────────────────────────────


class TestUpsert:
    def test_new_entity_created(self):
        t = EntityTracker()
        entity = t.upsert("テストNPC", "npc")
        assert entity.name == "テストNPC"
        assert entity.entity_type == "npc"
        assert t.count == 1

    def test_same_name_updates_existing(self):
        t = EntityTracker()
        t.upsert("アリス", "npc", {"hp": 10})
        t.upsert("アリス", "npc", {"hp": 8})
        assert t.count == 1
        entity = t.get("アリス")
        assert entity is not None
        assert entity.attributes["hp"] == 8

    def test_attributes_merged(self):
        t = EntityTracker()
        t.upsert("アリス", "npc", {"hp": 10, "weapon": "銃"})
        t.upsert("アリス", "npc", {"hp": 8, "status": "負傷"})
        entity = t.get("アリス")
        assert entity is not None
        assert entity.attributes["hp"] == 8
        assert entity.attributes["weapon"] == "銃"  # 既存属性は保持
        assert entity.attributes["status"] == "負傷"

    def test_notes_appended(self):
        t = EntityTracker()
        t.upsert("アリス", "npc", notes="祓魔師の少女")
        t.upsert("アリス", "npc", notes="実は記憶喪失")
        entity = t.get("アリス")
        assert entity is not None
        assert "祓魔師の少女" in entity.notes
        assert "実は記憶喪失" in entity.notes

    def test_notes_not_duplicated(self):
        t = EntityTracker()
        t.upsert("アリス", "npc", notes="祓魔師の少女")
        t.upsert("アリス", "npc", notes="祓魔師の少女")
        entity = t.get("アリス")
        assert entity is not None
        assert entity.notes.count("祓魔師の少女") == 1

    def test_first_seen_round_set_on_creation(self):
        t = EntityTracker()
        t.upsert("アリス", "npc", round_number=3)
        entity = t.get("アリス")
        assert entity is not None
        assert entity.first_seen_round == 3

    def test_last_updated_round_changes_on_update(self):
        t = EntityTracker()
        t.upsert("アリス", "npc", round_number=1)
        t.upsert("アリス", "npc", round_number=5)
        entity = t.get("アリス")
        assert entity is not None
        assert entity.first_seen_round == 1
        assert entity.last_updated_round == 5

    def test_default_entity_type_other(self):
        t = EntityTracker()
        entity = t.upsert("謎のもの")
        assert entity.entity_type == "other"

    def test_active_default_true(self):
        t = EntityTracker()
        entity = t.upsert("アリス", "npc")
        assert entity.active is True


# ──────────────────────────────────────
# get() のテスト
# ──────────────────────────────────────


class TestGet:
    def test_exact_match(self):
        t = _tracker_with_data()
        entity = t.get("山田神主")
        assert entity is not None
        assert entity.name == "山田神主"

    def test_partial_match(self):
        t = _tracker_with_data()
        entity = t.get("山田")  # 部分一致
        assert entity is not None
        assert "山田" in entity.name

    def test_not_found_returns_none(self):
        t = _tracker_with_data()
        assert t.get("存在しないキャラ") is None

    def test_case_insensitive(self):
        t = EntityTracker()
        t.upsert("Alice", "npc")
        entity = t.get("alice")
        assert entity is not None

    def test_reverse_partial_match(self):
        """検索ワードが名前を含む場合も一致する。"""
        t = EntityTracker()
        t.upsert("A", "npc")
        # "AB" が "A" を部分一致で見つける（"a" in "ab" → True）
        entity = t.get("AB")
        # 実装では key in query OR query in key の両方をチェック
        assert entity is not None


# ──────────────────────────────────────
# deactivate() のテスト
# ──────────────────────────────────────


class TestDeactivate:
    def test_deactivate_existing(self):
        t = _tracker_with_data()
        result = t.deactivate("山田神主")
        assert result is True
        entity = t.get("山田神主")
        assert entity is not None
        assert entity.active is False

    def test_deactivate_nonexistent(self):
        t = EntityTracker()
        result = t.deactivate("存在しない")
        assert result is False

    def test_deactivated_excluded_from_active_only(self):
        t = _tracker_with_data()
        t.deactivate("山田神主")
        npcs = t.get_all("npc", active_only=True)
        names = [e.name for e in npcs]
        assert "山田神主" not in names


# ──────────────────────────────────────
# get_all() のテスト
# ──────────────────────────────────────


class TestGetAll:
    def test_get_all_no_filter(self):
        t = _tracker_with_data()
        all_entities = t.get_all(active_only=False)
        assert len(all_entities) == 5

    def test_get_all_by_type_npc(self):
        t = _tracker_with_data()
        npcs = t.get_all("npc")
        assert len(npcs) == 2
        assert all(e.entity_type == "npc" for e in npcs)

    def test_get_all_by_type_item(self):
        t = _tracker_with_data()
        items = t.get_all("item")
        assert len(items) == 1
        assert items[0].name == "聖剣カグツチ"

    def test_get_all_active_only_excludes_inactive(self):
        t = _tracker_with_data()
        t.deactivate("封印解除")
        flags = t.get_all("quest_flag", active_only=True)
        assert len(flags) == 0

    def test_get_all_active_false_includes_inactive(self):
        t = _tracker_with_data()
        t.deactivate("封印解除")
        flags = t.get_all("quest_flag", active_only=False)
        assert len(flags) == 1


# ──────────────────────────────────────
# search() のテスト
# ──────────────────────────────────────


class TestSearch:
    def test_search_by_name(self):
        t = _tracker_with_data()
        results = t.search("山田")
        assert len(results) >= 1
        assert any("山田" in e.name for e in results)

    def test_search_by_notes(self):
        t = _tracker_with_data()
        results = t.search("神主")
        assert any("神社の神主" in e.notes for e in results)

    def test_search_by_attribute_value(self):
        t = _tracker_with_data()
        results = t.search("東京")
        assert any("東京" in str(e.attributes.get("region", "")) for e in results)

    def test_search_no_match(self):
        t = _tracker_with_data()
        assert t.search("xyznotfound") == []

    def test_search_case_insensitive(self):
        t = EntityTracker()
        t.upsert("Alice", "npc", notes="hunter")
        results = t.search("HUNTER")
        assert len(results) == 1


# ──────────────────────────────────────
# context_summary() のテスト
# ──────────────────────────────────────


class TestContextSummary:
    def test_summary_not_empty(self):
        t = _tracker_with_data()
        summary = t.context_summary()
        assert len(summary) > 0

    def test_summary_contains_npc_label(self):
        t = _tracker_with_data()
        summary = t.context_summary()
        assert "NPC" in summary

    def test_summary_contains_entity_names(self):
        t = _tracker_with_data()
        summary = t.context_summary()
        assert "山田神主" in summary

    def test_empty_tracker_returns_message(self):
        t = EntityTracker()
        summary = t.context_summary()
        assert "なし" in summary or "登録" in summary

    def test_summary_respects_max_per_type(self):
        t = EntityTracker()
        for i in range(10):
            t.upsert(f"NPC{i}", "npc")
        summary = t.context_summary(max_per_type=3)
        # 3 件以上表示されないこと（「他 N 件」が出るはず）
        assert "他" in summary

    def test_inactive_excluded_by_default(self):
        t = _tracker_with_data()
        t.deactivate("山田神主")
        summary = t.context_summary(active_only=True)
        assert "山田神主" not in summary


# ──────────────────────────────────────
# シリアライズ・永続化のテスト
# ──────────────────────────────────────


class TestSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        t = _tracker_with_data()
        t.deactivate("封印解除")
        d = t.to_dict()
        restored = EntityTracker.from_dict(d)

        assert restored.count == t.count
        entity = restored.get("山田神主")
        assert entity is not None
        assert entity.notes == "神社の神主"

    def test_save_and_load(self, tmp_path):
        t = _tracker_with_data()
        path = tmp_path / "entities.json"
        t.save(path)
        restored = EntityTracker.load(path)

        assert restored.count == t.count
        item = restored.get("聖剣カグツチ")
        assert item is not None
        assert item.entity_type == "item"

    def test_save_creates_parent_dir(self, tmp_path):
        t = EntityTracker()
        t.upsert("テスト", "npc")
        path = tmp_path / "sessions" / "s1" / "entities.json"
        t.save(path)
        assert path.exists()

    def test_active_flag_persisted(self, tmp_path):
        t = _tracker_with_data()
        t.deactivate("封印解除")
        path = tmp_path / "entities.json"
        t.save(path)
        restored = EntityTracker.load(path)
        flag = restored.get("封印解除")
        assert flag is not None
        assert flag.active is False


# ──────────────────────────────────────
# Entity.to_schema() のテスト
# ──────────────────────────────────────


class TestEntitySchema:
    def test_to_schema_returns_entity_record(self):
        from core.schemas import EntityRecord

        entity = Entity("テスト", "npc", {"hp": 5}, notes="メモ")
        schema = entity.to_schema()
        assert isinstance(schema, EntityRecord)
        assert schema.name == "テスト"
        assert schema.entity_type == "npc"
        assert schema.notes == "メモ"

    def test_short_description_contains_name(self):
        entity = Entity("山田神主", "npc", {}, notes="神社の神主")
        desc = entity.short_description()
        assert "山田神主" in desc

    def test_short_description_truncates_long_notes(self):
        entity = Entity("テスト", "npc", notes="あ" * 100)
        desc = entity.short_description()
        assert len(desc) < 200  # 適度に短い

    def test_short_description_inactive_label(self):
        entity = Entity("テスト", "npc", active=False)
        desc = entity.short_description()
        assert "非アクティブ" in desc


# ──────────────────────────────────────
# from_schema_list() のテスト
# ──────────────────────────────────────


class TestFromSchemaList:
    def test_registers_all_records(self):
        from core.schemas import EntityRecord

        t = EntityTracker()
        records = [
            EntityRecord(name="Alice", entity_type="npc"),
            EntityRecord(name="魔法の剣", entity_type="item"),
        ]
        entities = t.from_schema_list(records, round_number=2)
        assert len(entities) == 2
        assert t.count == 2

    def test_inactive_record_deactivates(self):
        from core.schemas import EntityRecord

        t = EntityTracker()
        records = [
            EntityRecord(name="倒された敵", entity_type="npc", active=False),
        ]
        t.from_schema_list(records)
        entity = t.get("倒された敵")
        assert entity is not None
        assert entity.active is False
