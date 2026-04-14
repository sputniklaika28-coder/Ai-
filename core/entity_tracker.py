"""entity_tracker.py — TRPG セッションのエンティティ永続追跡。

セッション中に登場したすべてのエンティティ（NPC・アイテム・場所・クエストフラグ）を
追跡・更新・照会する。MemoryManager の「テキスト要約のみ」という弱点を補完する。

GMDirector がナレーション生成前にこのデータを LLM コンテキストに注入することで、
「3 セッション前に登場した NPC の名前を忘れる」問題を解決する。

使用例::
    tracker = EntityTracker()
    tracker.upsert("山田神主", "npc", {"disposition": "友好的", "hp": None},
                   notes="神社の神主。封印の場所を知っている。")
    tracker.upsert("聖剣カグツチ", "item", {"location": "アリスの所持品"})
    print(tracker.context_summary())
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

EntityType = Literal["npc", "item", "location", "quest_flag", "other"]

_ENTITY_TYPE_LABELS: dict[str, str] = {
    "npc": "NPC",
    "item": "アイテム",
    "location": "場所",
    "quest_flag": "クエスト",
    "other": "その他",
}


# ──────────────────────────────────────
# Entity データクラス
# ──────────────────────────────────────


@dataclass
class Entity:
    """セッション中に登場した 1 つのエンティティ。

    Args:
        name: エンティティ名。EntityTracker 内で一意キーとして使用。
        entity_type: 種別 ('npc' / 'item' / 'location' / 'quest_flag' / 'other')。
        attributes: 任意属性辞書（HP・場所・状態などゲーム固有データ）。
        notes: GM 用メモ（プレイヤーに非公開の情報も記録可）。
        first_seen_round: 初回登場ラウンド（0 = 開始前）。
        last_updated_round: 最終更新ラウンド。
        active: アクティブか否か（撃破・回収済みは False）。
    """

    name: str
    entity_type: EntityType
    attributes: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    first_seen_round: int = 0
    last_updated_round: int = 0
    active: bool = True

    # ── シリアライズ ──

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entity_type": self.entity_type,
            "attributes": dict(self.attributes),
            "notes": self.notes,
            "first_seen_round": self.first_seen_round,
            "last_updated_round": self.last_updated_round,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entity":
        return cls(
            name=d["name"],
            entity_type=d.get("entity_type", "other"),
            attributes=dict(d.get("attributes", {})),
            notes=d.get("notes", ""),
            first_seen_round=d.get("first_seen_round", 0),
            last_updated_round=d.get("last_updated_round", 0),
            active=d.get("active", True),
        )

    def to_schema(self) -> "EntityRecord":
        """core.schemas.EntityRecord へ変換する。"""
        from core.schemas import EntityRecord

        return EntityRecord(
            name=self.name,
            entity_type=self.entity_type,
            attributes=self.attributes,
            notes=self.notes,
            active=self.active,
        )

    def short_description(self) -> str:
        """コンテキスト注入用の1行説明。"""
        parts = [f"【{self.name}】"]
        if self.notes:
            parts.append(self.notes[:60] + ("…" if len(self.notes) > 60 else ""))
        for k, v in list(self.attributes.items())[:3]:
            parts.append(f"{k}:{v}")
        if not self.active:
            parts.append("(非アクティブ)")
        return " ".join(parts)


# ──────────────────────────────────────
# EntityTracker 本体
# ──────────────────────────────────────


class EntityTracker:
    """セッション中に登場したすべてのエンティティを管理するリポジトリ。

    - upsert(): 新規追加または属性マージによる更新
    - get(): 名前検索（完全一致 → 部分一致）
    - get_all(): 種別・アクティブ状態でフィルタ
    - search(): 名前・ノート・属性値のフリーテキスト検索
    - context_summary(): LLM プロンプトに注入できるテキスト生成
    - save() / load(): JSON ファイル永続化
    """

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}  # key = name (lowercase)

    # ── CRUD ──────────────────────────────────────

    def upsert(
        self,
        name: str,
        entity_type: EntityType = "other",
        attributes: dict[str, Any] | None = None,
        notes: str = "",
        round_number: int = 0,
    ) -> Entity:
        """エンティティを追加または更新する。

        既存エンティティがある場合は属性をマージ（上書き）し、
        notes が空でなければ追記する。

        Args:
            name: エンティティ名（大文字小文字は保持するが検索は無視）。
            entity_type: 種別。
            attributes: 追加・更新する属性。
            notes: GM メモ（既存メモに追記される）。
            round_number: 現在のラウンド数（更新記録用）。

        Returns:
            作成または更新された Entity。
        """
        key = name.lower()
        existing = self._entities.get(key)

        if existing is None:
            entity = Entity(
                name=name,
                entity_type=entity_type,
                attributes=dict(attributes or {}),
                notes=notes,
                first_seen_round=round_number,
                last_updated_round=round_number,
            )
            self._entities[key] = entity
            logger.info("EntityTracker: 新規登録 [%s] '%s'", entity_type, name)
        else:
            # 属性マージ
            if attributes:
                existing.attributes.update(attributes)
            # ノート追記
            if notes and notes not in existing.notes:
                existing.notes = (
                    f"{existing.notes}\n{notes}".strip() if existing.notes else notes
                )
            existing.last_updated_round = round_number
            entity = existing
            logger.debug("EntityTracker: 更新 '%s'", name)

        return entity

    def get(self, name: str) -> Entity | None:
        """名前でエンティティを取得する（完全一致 → 部分一致の順）。"""
        key = name.lower()
        if key in self._entities:
            return self._entities[key]
        # 部分一致
        for k, v in self._entities.items():
            if key in k or k in key:
                return v
        return None

    def deactivate(self, name: str) -> bool:
        """エンティティを非アクティブ化する（撃破・消耗・完了）。

        Returns:
            対象が見つかった場合 True。
        """
        entity = self.get(name)
        if entity is None:
            return False
        entity.active = False
        logger.info("EntityTracker: 非アクティブ化 '%s'", name)
        return True

    def get_all(
        self,
        entity_type: EntityType | None = None,
        active_only: bool = True,
    ) -> list[Entity]:
        """フィルタ条件に合うエンティティ一覧を返す。

        Args:
            entity_type: 指定した場合その種別のみ返す。
            active_only: True の場合アクティブなものだけ返す。
        """
        result = list(self._entities.values())
        if entity_type is not None:
            result = [e for e in result if e.entity_type == entity_type]
        if active_only:
            result = [e for e in result if e.active]
        return result

    def search(self, query: str) -> list[Entity]:
        """名前・ノート・属性値にクエリ文字列が含まれるエンティティを返す。

        大文字小文字を区別しない。
        """
        q = query.lower()
        results: list[Entity] = []
        for entity in self._entities.values():
            # 名前
            if q in entity.name.lower():
                results.append(entity)
                continue
            # ノート
            if q in entity.notes.lower():
                results.append(entity)
                continue
            # 属性値
            if any(q in str(v).lower() for v in entity.attributes.values()):
                results.append(entity)
        return results

    @property
    def count(self) -> int:
        """登録エンティティ数（アクティブ・非アクティブ含む）。"""
        return len(self._entities)

    # ── コンテキスト生成 ──────────────────────

    def context_summary(
        self,
        max_per_type: int = 5,
        active_only: bool = True,
    ) -> str:
        """LLM プロンプトに注入するエンティティサマリー文字列を返す。

        種別ごとにグループ化し、最大 max_per_type 件ずつ表示する。

        Args:
            max_per_type: 種別ごとの最大表示数。
            active_only: True の場合アクティブなものだけ含める。
        """
        if not self._entities:
            return "（登録済みエンティティなし）"

        lines: list[str] = ["【セッション登場エンティティ】"]
        for etype in ("npc", "item", "location", "quest_flag", "other"):
            entities = self.get_all(entity_type=etype, active_only=active_only)  # type: ignore[arg-type]
            if not entities:
                continue
            label = _ENTITY_TYPE_LABELS.get(etype, etype)
            lines.append(f"▸ {label}")
            for e in entities[:max_per_type]:
                lines.append(f"  {e.short_description()}")
            if len(entities) > max_per_type:
                lines.append(f"  （他 {len(entities) - max_per_type} 件）")

        return "\n".join(lines)

    # ── シリアライズ ──────────────────────────

    def to_dict(self) -> dict:
        return {
            "entities": [e.to_dict() for e in self._entities.values()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EntityTracker":
        tracker = cls()
        for item in d.get("entities", []):
            entity = Entity.from_dict(item)
            tracker._entities[entity.name.lower()] = entity
        return tracker

    def save(self, path: str | Path) -> None:
        """エンティティデータをファイルに保存する。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("EntityTracker: 保存 → %s (%d 件)", path, self.count)

    @classmethod
    def load(cls, path: str | Path) -> "EntityTracker":
        """ファイルからエンティティデータを読み込む。"""
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        tracker = cls.from_dict(data)
        logger.info("EntityTracker: 読み込み ← %s (%d 件)", path, tracker.count)
        return tracker

    def from_schema_list(
        self,
        records: list["EntityRecord"],
        round_number: int = 0,
    ) -> list[Entity]:
        """EntityRecord リストを一括で upsert し、登録した Entity リストを返す。"""
        result: list[Entity] = []
        for rec in records:
            entity = self.upsert(
                name=rec.name,
                entity_type=rec.entity_type,  # type: ignore[arg-type]
                attributes=dict(rec.attributes),
                notes=rec.notes,
                round_number=round_number,
            )
            if not rec.active:
                entity.active = False
            result.append(entity)
        return result
