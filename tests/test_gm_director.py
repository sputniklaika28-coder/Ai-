"""tests/test_gm_director.py — GMDirector のユニットテスト。

LMClient をモックして外部 LLM 依存なしに動作を検証する。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.entity_tracker import EntityTracker
from core.game_state import CombatantState, GameState
from core.gm_director import GMDirector, GMDirectorConfig, GMTurnResult
from core.schemas import (
    EntityExtractionResult,
    EntityRecord,
    GameIntention,
    SessionContext,
)


# ──────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────


def _make_game_state() -> GameState:
    gs = GameState()
    gs.add_combatant(CombatantState("アリス", hp=10, max_hp=10, initiative=5, body=4, skill=4))
    gs.add_combatant(CombatantState("ゴブリン", hp=6, max_hp=6, initiative=3, is_enemy=True, body=2))
    return gs


def _make_lm_client(
    intention: GameIntention | None = None,
    narration: str = "テストナレーション",
    entities: list[EntityRecord] | None = None,
) -> MagicMock:
    """generate_structured と generate_response をモックした LMClient を返す。"""
    client = MagicMock()

    async def _generate_structured(system_prompt, user_message, schema, **kwargs):
        if schema is GameIntention:
            return intention or GameIntention(actor="アリス", action_type="attack", target="ゴブリン")
        if schema is EntityExtractionResult:
            return EntityExtractionResult(entities=entities or [])
        return None

    async def _generate_response(system_prompt, user_message, **kwargs):
        return narration, []

    client.generate_structured = _generate_structured
    client.generate_response = _generate_response
    return client


def _make_director(
    game_state: GameState | None = None,
    entity_tracker: EntityTracker | None = None,
    lm_client: Any = None,
    config: GMDirectorConfig | None = None,
    memory_manager: Any = None,
) -> GMDirector:
    gs = game_state or _make_game_state()
    et = entity_tracker or EntityTracker()
    lm = lm_client or _make_lm_client()
    return GMDirector(lm, gs, et, config=config, memory_manager=memory_manager)


# ──────────────────────────────────────
# process_turn() — 基本動作
# ──────────────────────────────────────


class TestProcessTurnBasic:
    @pytest.mark.asyncio
    async def test_returns_gm_turn_result(self):
        director = _make_director()
        result = await director.process_turn("ゴブリンに攻撃する", "アリス")
        assert isinstance(result, GMTurnResult)

    @pytest.mark.asyncio
    async def test_narration_populated(self):
        director = _make_director(lm_client=_make_lm_client(narration="剣を振り下ろした！"))
        result = await director.process_turn("攻撃する", "アリス")
        assert result.narration == "剣を振り下ろした！"

    @pytest.mark.asyncio
    async def test_intention_parsed(self):
        intention = GameIntention(actor="アリス", action_type="attack", target="ゴブリン")
        director = _make_director(lm_client=_make_lm_client(intention=intention))
        result = await director.process_turn("攻撃する", "アリス")
        assert result.intention is not None
        assert result.intention.action_type == "attack"

    @pytest.mark.asyncio
    async def test_error_none_on_success(self):
        director = _make_director()
        result = await director.process_turn("探索する", "アリス")
        assert result.error is None

    @pytest.mark.asyncio
    async def test_vtt_chat_lines_populated(self):
        director = _make_director(lm_client=_make_lm_client(narration="ナレーション"))
        result = await director.process_turn("行動する", "アリス")
        assert len(result.vtt_chat_lines) >= 1
        assert "ナレーション" in result.vtt_chat_lines[0]


# ──────────────────────────────────────
# process_turn() — 戦闘フェーズ自動解決
# ──────────────────────────────────────


class TestProcessTurnCombat:
    @pytest.mark.asyncio
    async def test_combat_auto_resolve_in_combat_phase(self):
        """combat フェーズで攻撃アクションが CombatEngine で解決されること。"""
        gs = _make_game_state()
        gs.start_combat()

        intention = GameIntention(actor="アリス", action_type="attack", target="ゴブリン")
        director = _make_director(
            game_state=gs,
            lm_client=_make_lm_client(intention=intention),
        )
        result = await director.process_turn("ゴブリンを攻撃！", "アリス")

        # 戦闘が解決されていること
        assert result.combat_result is not None
        assert result.combat_result.actor == "アリス"

    @pytest.mark.asyncio
    async def test_no_combat_resolve_in_exploration(self):
        """exploration フェーズでは auto_resolve されないこと。"""
        gs = _make_game_state()
        # phase = "exploration" (デフォルト)

        intention = GameIntention(actor="アリス", action_type="attack", target="ゴブリン")
        director = _make_director(
            game_state=gs,
            lm_client=_make_lm_client(intention=intention),
        )
        result = await director.process_turn("攻撃する", "アリス")
        assert result.combat_result is None

    @pytest.mark.asyncio
    async def test_combat_result_in_vtt_lines_when_hp_changed(self):
        """HP 変化があった場合 vtt_chat_lines に HP 情報が含まれること。"""
        gs = _make_game_state()
        gs.start_combat()

        intention = GameIntention(actor="アリス", action_type="attack", target="ゴブリン")
        director = _make_director(
            game_state=gs,
            lm_client=_make_lm_client(intention=intention),
        )
        result = await director.process_turn("攻撃！", "アリス")

        # 成功してダメージが入れば HP 行が追加される
        if result.combat_result and result.combat_result.hp_after is not None:
            hp_line = next(
                (l for l in result.vtt_chat_lines if "HP" in l or "残" in l), None
            )
            assert hp_line is not None

    @pytest.mark.asyncio
    async def test_auto_resolve_disabled_by_config(self):
        """auto_resolve_combat=False の場合は解決しないこと。"""
        gs = _make_game_state()
        gs.start_combat()

        config = GMDirectorConfig(auto_resolve_combat=False)
        intention = GameIntention(actor="アリス", action_type="attack", target="ゴブリン")
        director = _make_director(
            game_state=gs,
            lm_client=_make_lm_client(intention=intention),
            config=config,
        )
        result = await director.process_turn("攻撃する", "アリス")
        assert result.combat_result is None


# ──────────────────────────────────────
# process_turn() — エンティティ自動抽出
# ──────────────────────────────────────


class TestProcessTurnEntityExtraction:
    @pytest.mark.asyncio
    async def test_new_entities_extracted(self):
        """ナレーションから新規エンティティが抽出されること。"""
        new_records = [
            EntityRecord(name="老神主", entity_type="npc", notes="謎の老人"),
        ]
        director = _make_director(lm_client=_make_lm_client(entities=new_records))

        result = await director.process_turn("老神主に話しかける", "アリス")
        assert len(result.new_entities) == 1
        assert result.new_entities[0].name == "老神主"

    @pytest.mark.asyncio
    async def test_new_entities_registered_in_tracker(self):
        """抽出されたエンティティが EntityTracker に登録されること。"""
        et = EntityTracker()
        new_records = [EntityRecord(name="魔法の剣", entity_type="item")]
        director = _make_director(
            entity_tracker=et,
            lm_client=_make_lm_client(entities=new_records),
        )

        await director.process_turn("剣を発見した", "アリス")
        assert et.get("魔法の剣") is not None

    @pytest.mark.asyncio
    async def test_no_entities_when_auto_extract_disabled(self):
        """auto_extract_entities=False の場合は抽出しないこと。"""
        config = GMDirectorConfig(auto_extract_entities=False)
        new_records = [EntityRecord(name="NPC", entity_type="npc")]
        director = _make_director(
            lm_client=_make_lm_client(entities=new_records),
            config=config,
        )

        result = await director.process_turn("誰かに会う", "アリス")
        assert result.new_entities == []


# ──────────────────────────────────────
# build_context_block() のテスト
# ──────────────────────────────────────


class TestBuildContextBlock:
    def test_includes_game_state_summary(self):
        gs = _make_game_state()
        director = _make_director(game_state=gs)
        block = director.build_context_block()
        # GameState.summary() にはフェーズが含まれる
        assert "exploration" in block or "combat" in block

    def test_includes_entity_summary(self):
        et = EntityTracker()
        et.upsert("謎のNPC", "npc", notes="重要人物")
        director = _make_director(entity_tracker=et)
        block = director.build_context_block()
        assert "謎のNPC" in block

    def test_extra_context_appended(self):
        director = _make_director()
        block = director.build_context_block(extra="特別なヒント")
        assert "特別なヒント" in block

    def test_empty_entity_tracker_handled(self):
        director = _make_director(entity_tracker=EntityTracker())
        block = director.build_context_block()
        # エラーにならないこと
        assert isinstance(block, str)

    def test_game_state_injection_disabled(self):
        config = GMDirectorConfig(inject_game_state=False, inject_entities=False)
        gs = _make_game_state()
        director = _make_director(game_state=gs, config=config)
        block = director.build_context_block()
        # ゲーム状態は含まれないが、エラーにもならない
        assert isinstance(block, str)


# ──────────────────────────────────────
# get_session_context() のテスト
# ──────────────────────────────────────


class TestGetSessionContext:
    def test_returns_session_context(self):
        director = _make_director()
        ctx = director.get_session_context()
        assert isinstance(ctx, SessionContext)

    def test_phase_matches_game_state(self):
        gs = _make_game_state()
        director = _make_director(game_state=gs)
        ctx = director.get_session_context()
        assert ctx.phase == gs.phase

    def test_round_number_matches_game_state(self):
        gs = _make_game_state()
        gs.start_combat()
        director = _make_director(game_state=gs)
        ctx = director.get_session_context()
        assert ctx.round_number == 1

    def test_entity_summary_in_context(self):
        et = EntityTracker()
        et.upsert("鈴木刑事", "npc")
        director = _make_director(entity_tracker=et)
        ctx = director.get_session_context()
        assert "鈴木刑事" in ctx.entity_summary


# ──────────────────────────────────────
# 直近イベント追跡のテスト
# ──────────────────────────────────────


class TestRecentEvents:
    @pytest.mark.asyncio
    async def test_recent_events_accumulate(self):
        director = _make_director()
        await director.process_turn("行動1", "アリス")
        await director.process_turn("行動2", "アリス")

        ctx = director.get_session_context()
        assert len(ctx.recent_events) == 2

    @pytest.mark.asyncio
    async def test_recent_events_capped_by_config(self):
        config = GMDirectorConfig(max_recent_events=2)
        director = _make_director(config=config)

        for i in range(5):
            await director.process_turn(f"行動{i}", "アリス")

        ctx = director.get_session_context()
        assert len(ctx.recent_events) <= 2


# ──────────────────────────────────────
# GMTurnResult.to_schema() のテスト
# ──────────────────────────────────────


class TestGMTurnResultToSchema:
    @pytest.mark.asyncio
    async def test_to_schema_returns_gm_turn_result_schema(self):
        from core.schemas import GMTurnResultSchema

        director = _make_director()
        result = await director.process_turn("行動する", "アリス")
        schema = result.to_schema()
        assert isinstance(schema, GMTurnResultSchema)
        assert schema.narration != ""

    @pytest.mark.asyncio
    async def test_to_schema_combat_resolved_flag(self):
        gs = _make_game_state()
        gs.start_combat()
        intention = GameIntention(actor="アリス", action_type="attack", target="ゴブリン")
        director = _make_director(
            game_state=gs,
            lm_client=_make_lm_client(intention=intention),
        )
        result = await director.process_turn("攻撃！", "アリス")
        schema = result.to_schema()
        assert schema.combat_resolved is True

    @pytest.mark.asyncio
    async def test_to_schema_new_entity_names(self):
        new_records = [EntityRecord(name="謎の仮面", entity_type="item")]
        director = _make_director(lm_client=_make_lm_client(entities=new_records))
        result = await director.process_turn("仮面を見つける", "アリス")
        schema = result.to_schema()
        assert "謎の仮面" in schema.new_entity_names


# ──────────────────────────────────────
# LLM 失敗時のフォールバックテスト
# ──────────────────────────────────────


class TestFallback:
    @pytest.mark.asyncio
    async def test_narration_fallback_on_lm_error(self):
        """generate_response が例外を投げても GMTurnResult が返ること。"""
        client = MagicMock()

        async def _generate_structured(system_prompt, user_message, schema, **kwargs):
            return GameIntention(actor="アリス", action_type="other")

        async def _generate_response(*args, **kwargs):
            raise RuntimeError("LM 接続失敗")

        client.generate_structured = _generate_structured
        client.generate_response = _generate_response

        director = _make_director(lm_client=client)
        result = await director.process_turn("何かする", "アリス")
        # エラーになっても narration が空でないこと（フォールバック）
        assert result.narration is not None

    @pytest.mark.asyncio
    async def test_intention_fallback_when_structured_fails(self):
        """generate_structured が None を返しても process_turn が完了すること。"""
        client = MagicMock()

        async def _generate_structured(system_prompt, user_message, schema, **kwargs):
            return None  # 解釈失敗

        async def _generate_response(*args, **kwargs):
            return "フォールバックナレーション", []

        client.generate_structured = _generate_structured
        client.generate_response = _generate_response

        director = _make_director(lm_client=client)
        result = await director.process_turn("テスト", "アリス")
        # フォールバック意図 (action_type="other") が設定されること
        assert result.intention is not None
        assert result.error is None


# ──────────────────────────────────────
# build_context_block() — MemoryManager 統合
# ──────────────────────────────────────


class TestBuildContextBlockWithMemory:
    def _make_memory(self, messages: list[tuple[str, str]] | None = None):
        from core.memory_manager import MemoryManager
        mm = MemoryManager(lm_client=None)
        for speaker, body in (messages or []):
            mm.add_message(speaker, body)
        return mm

    def test_memory_context_injected(self):
        """MemoryManager にメッセージがある場合、context_block に会話履歴が含まれること。"""
        mm = self._make_memory([("アリス", "扉を開ける"), ("GM", "軋む音がした")])
        director = _make_director(memory_manager=mm)
        block = director.build_context_block()
        assert "アリス" in block or "直近の会話" in block

    def test_no_memory_manager(self):
        """MemoryManager が None の場合、従来通り動作すること。"""
        director = _make_director()
        block = director.build_context_block()
        # エラーにならず文字列を返す
        assert isinstance(block, str)

    def test_inject_memory_disabled(self):
        """inject_memory=False の場合、メモリコンテキストが注入されないこと。"""
        mm = self._make_memory([("アリス", "探索する"), ("GM", "廃墟が見えた")])
        config = GMDirectorConfig(inject_memory=False)
        director = _make_director(config=config, memory_manager=mm)
        block = director.build_context_block()
        # MemoryManager の内容が含まれないこと
        assert "直近の会話" not in block
        assert "これまでのあらすじ" not in block

    def test_set_memory_manager_late(self):
        """set_memory_manager() で後から注入した MemoryManager が反映されること。"""
        director = _make_director()
        mm = self._make_memory([("ボブ", "罠を調べる")])
        director.set_memory_manager(mm)
        block = director.build_context_block()
        assert "ボブ" in block or "直近の会話" in block

    @pytest.mark.asyncio
    async def test_process_turn_includes_memory_in_context_injected(self):
        """process_turn() の context_injected にメモリコンテキストが含まれること。"""
        mm = self._make_memory([("アリス", "洞窟に入る")])
        director = _make_director(
            lm_client=_make_lm_client(narration="暗闇が広がる"),
            memory_manager=mm,
        )
        result = await director.process_turn("進む", "アリス")
        assert "アリス" in result.context_injected or "直近の会話" in result.context_injected
