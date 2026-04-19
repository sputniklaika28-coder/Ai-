"""addon.py — GMDirector アドオン (Phase 4)。

LLM が呼び出せるツール群:
  process_player_turn  — プレイヤーの 1 ターンを統合処理
  get_session_context  — 現在のセッションコンテキスト取得
  upsert_entity        — エンティティ手動追加・更新
  get_entity           — エンティティ照会
  list_entities        — エンティティ一覧取得
  deactivate_entity    — エンティティを非アクティブ化

依存: combat_engine アドオン（GameState・CombatEngine を借用）
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from core.addons.addon_base import AddonBase, AddonContext, ToolExecutionContext
except ModuleNotFoundError:
    from addons.addon_base import AddonBase, AddonContext, ToolExecutionContext  # type: ignore


class GMDirectorAddon(AddonBase):
    """GMDirector + EntityTracker を提供するアドオン。"""

    def __init__(self) -> None:
        self._director: Any | None = None    # GMDirector
        self._entities: Any | None = None   # EntityTracker
        self._entity_path: Path | None = None

    # ──────────────────────────────────────
    # ライフサイクル
    # ──────────────────────────────────────

    def on_load(self, context: AddonContext) -> None:
        from core.entity_tracker import EntityTracker
        from core.game_state import GameState
        from core.gm_director import GMDirector, GMDirectorConfig

        # EntityTracker の初期化・復元
        session_dir = getattr(context, "session_dir", None)
        if session_dir:
            self._entity_path = Path(session_dir) / "entities.json"
            if self._entity_path.exists():
                try:
                    self._entities = EntityTracker.load(self._entity_path)
                    logger.info("GMDirectorAddon: entities.json から復元 (%d 件)", self._entities.count)
                except Exception as e:
                    logger.warning("GMDirectorAddon: エンティティ復元失敗 → 新規作成: %s", e)
                    self._entities = EntityTracker()
            else:
                self._entities = EntityTracker()
        else:
            self._entities = EntityTracker()

        # GameState は combat_engine アドオンから借用する試み
        game_state = self._get_game_state_from_context(context)

        # GMDirector の初期化
        config = GMDirectorConfig(
            auto_resolve_combat=True,
            inject_game_state=True,
            inject_entities=True,
            auto_extract_entities=True,
        )
        self._director = GMDirector(
            lm_client=context.lm_client,
            game_state=game_state,
            entity_tracker=self._entities,
            config=config,
        )
        logger.info("GMDirectorAddon: 初期化完了")

    def _get_game_state_from_context(self, context: AddonContext) -> Any:
        """combat_engine アドオンが持つ GameState を取得する。フォールバックは新規作成。"""
        from core.game_state import GameState

        try:
            addon_manager = getattr(context, "addon_manager", None)
            if addon_manager:
                combat_addon = getattr(addon_manager, "get_addon", lambda _: None)("combat_engine")
                if combat_addon and hasattr(combat_addon, "_game_state"):
                    return combat_addon._game_state
        except Exception:
            pass
        return GameState()

    def on_unload(self) -> None:
        self._save_entities()
        self._director = None
        self._entities = None

    def _save_entities(self) -> None:
        if self._entities and self._entity_path:
            try:
                self._entities.save(self._entity_path)
            except Exception as e:
                logger.error("GMDirectorAddon: エンティティ保存失敗: %s", e)

    # ──────────────────────────────────────
    # ツール定義
    # ──────────────────────────────────────

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "process_player_turn",
                    "description": (
                        "プレイヤーの 1 ターンを統合処理する。"
                        "行動意図の解釈 → 戦闘解決 → ナレーション生成 → エンティティ更新 を自動実行する。"
                        "GM がプレイヤーのメッセージに応答する際はこのツールを使うこと。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "player_message": {
                                "type": "string",
                                "description": "プレイヤーのチャットメッセージ",
                            },
                            "character_name": {
                                "type": "string",
                                "description": "プレイヤーキャラクター名",
                            },
                            "extra_context": {
                                "type": "string",
                                "description": "追加コンテキスト（任意）",
                            },
                        },
                        "required": ["player_message"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_session_context",
                    "description": (
                        "現在のセッションコンテキストを取得する。"
                        "GameState サマリー・エンティティ一覧・直近イベントを含む。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "upsert_entity",
                    "description": (
                        "エンティティ（NPC・アイテム・場所・クエストフラグ）を追加または更新する。"
                        "新しい NPC が登場したとき、アイテムを発見したときなどに使用する。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "エンティティ名",
                            },
                            "entity_type": {
                                "type": "string",
                                "enum": ["npc", "item", "location", "quest_flag", "other"],
                                "description": "種別",
                            },
                            "attributes": {
                                "type": "object",
                                "description": "属性辞書 (例: {\"disposition\": \"敵対的\", \"hp\": 8})",
                            },
                            "notes": {
                                "type": "string",
                                "description": "GM メモ（背景・秘密・プロットフック等）",
                            },
                        },
                        "required": ["name", "entity_type"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_entity",
                    "description": "名前でエンティティを照会する（部分一致対応）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "検索するエンティティ名（部分一致可）",
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_entities",
                    "description": "登録済みエンティティの一覧を取得する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_type": {
                                "type": "string",
                                "enum": ["npc", "item", "location", "quest_flag", "other"],
                                "description": "フィルタする種別（省略時は全種別）",
                            },
                            "active_only": {
                                "type": "boolean",
                                "description": "True の場合アクティブなもののみ返す（デフォルト: true）",
                            },
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "deactivate_entity",
                    "description": (
                        "エンティティを非アクティブ化する。"
                        "敵を撃破した・アイテムを消耗した・クエストが完了したときに使用する。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "非アクティブ化するエンティティ名",
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
        ]

    # ──────────────────────────────────────
    # ツール実行
    # ──────────────────────────────────────

    def execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        context: ToolExecutionContext,
    ) -> tuple[bool, str | None]:
        dispatch = {
            "process_player_turn": self._exec_process_turn,
            "get_session_context": self._exec_get_context,
            "upsert_entity": self._exec_upsert_entity,
            "get_entity": self._exec_get_entity,
            "list_entities": self._exec_list_entities,
            "deactivate_entity": self._exec_deactivate_entity,
        }
        fn = dispatch.get(tool_name)
        if fn is None:
            return False, json.dumps(
                {"error": f"未知のツール: {tool_name}"}, ensure_ascii=False
            )
        try:
            return fn(tool_args, context)
        except Exception as e:
            logger.exception("GMDirectorAddon: ツール実行エラー [%s]: %s", tool_name, e)
            return False, json.dumps({"error": str(e)}, ensure_ascii=False)

    # ── 個別ツール実装 ──────────────────

    def _exec_process_turn(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._director is None:
            return False, json.dumps({"error": "GMDirector 未初期化"}, ensure_ascii=False)

        player_message = args.get("player_message", "")
        character_name = args.get("character_name", "")
        extra_context = args.get("extra_context", "")

        try:
            result = asyncio.run(
                self._director.process_turn(player_message, character_name, extra_context)
            )
        except RuntimeError:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self._director.process_turn(player_message, character_name, extra_context),
                )
                result = future.result(timeout=120)

        self._save_entities()

        schema = result.to_schema()
        return True, schema.model_dump_json(ensure_ascii=False)

    def _exec_get_context(self, _args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._director is None:
            return False, json.dumps({"error": "GMDirector 未初期化"}, ensure_ascii=False)
        ctx = self._director.get_session_context()
        return True, ctx.model_dump_json(ensure_ascii=False)

    def _exec_upsert_entity(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._entities is None:
            return False, json.dumps({"error": "EntityTracker 未初期化"}, ensure_ascii=False)

        name = args.get("name", "")
        if not name:
            return False, json.dumps({"error": "name は必須です"}, ensure_ascii=False)

        entity = self._entities.upsert(
            name=name,
            entity_type=args.get("entity_type", "other"),
            attributes=args.get("attributes", {}),
            notes=args.get("notes", ""),
        )
        self._save_entities()
        return True, json.dumps({
            "name": entity.name,
            "entity_type": entity.entity_type,
            "active": entity.active,
            "attributes": entity.attributes,
        }, ensure_ascii=False)

    def _exec_get_entity(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._entities is None:
            return False, json.dumps({"error": "EntityTracker 未初期化"}, ensure_ascii=False)

        name = args.get("name", "")
        entity = self._entities.get(name)
        if entity is None:
            return False, json.dumps(
                {"error": f"エンティティ '{name}' が見つかりません"}, ensure_ascii=False
            )
        return True, json.dumps(entity.to_dict(), ensure_ascii=False)

    def _exec_list_entities(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._entities is None:
            return False, json.dumps({"error": "EntityTracker 未初期化"}, ensure_ascii=False)

        entity_type = args.get("entity_type")
        active_only = args.get("active_only", True)
        entities = self._entities.get_all(entity_type=entity_type, active_only=active_only)
        return True, json.dumps(
            {"entities": [e.to_dict() for e in entities], "count": len(entities)},
            ensure_ascii=False,
        )

    def _exec_deactivate_entity(self, args: dict, _ctx: Any) -> tuple[bool, str]:
        if self._entities is None:
            return False, json.dumps({"error": "EntityTracker 未初期化"}, ensure_ascii=False)

        name = args.get("name", "")
        success = self._entities.deactivate(name)
        self._save_entities()
        if success:
            return True, json.dumps({"deactivated": name}, ensure_ascii=False)
        return False, json.dumps(
            {"error": f"エンティティ '{name}' が見つかりません"}, ensure_ascii=False
        )
