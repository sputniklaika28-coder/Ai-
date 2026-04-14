"""tests/test_persona_builder.py — PersonaBuilder のユニットテスト。

LMClient を Mock して、外部 LLM への依存なしに動作を検証する。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.schemas import (
    CharacterConceptOutput,
    InitialStats,
    PersonaDefinition,
    SkillEntry,
)


# ──────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────


def _make_concept(**overrides: Any) -> CharacterConceptOutput:
    """テスト用 CharacterConceptOutput を生成する。"""
    defaults: dict[str, Any] = {
        "name": "鈴木アリス",
        "archetype": "斥候",
        "background": "孤独な幼少期を過ごした元軍人。",
        "personality": "無口で感情を表に出さない。",
        "speech_style": "敬語だが端的。「〜です」「〜ます」で締める。",
        "motivation": "仲間を守るために戦い続ける。",
        "forbidden_actions": ["無辜の民を傷つける", "嘘をつく"],
        "initial_stats": InitialStats(hp=5, sp=3, body=4, soul=2, skill=5, magic=1, mobility=4, armor=1),
        "recommended_skills": [
            SkillEntry(name="精密射撃", cost=2, condition="装備:銃", effect="命中+2"),
        ],
        "appearance": "銀髪ショートの女性。黒いコートを着用。",
        "portrait_keywords": ["young woman", "silver hair", "black coat", "anime style"],
    }
    defaults.update(overrides)
    return CharacterConceptOutput(**defaults)


def _make_persona(**overrides: Any) -> PersonaDefinition:
    """テスト用 PersonaDefinition を生成する。"""
    defaults: dict[str, Any] = {
        "character_name": "鈴木アリス",
        "system_prompt": "あなたは鈴木アリス（斥候）として振る舞ってください。",
        "speech_style_examples": ["「了解です。」", "「任務を遂行します。」"],
        "forbidden_topics": ["キャラクターを外れた発言"],
        "persona_summary": "鈴木アリス（斥候）— 無口な銀髪の元軍人",
    }
    defaults.update(overrides)
    return PersonaDefinition(**defaults)


def _make_lm_client(concept: CharacterConceptOutput | None, persona: PersonaDefinition | None) -> MagicMock:
    """generate_structured を Mock した LMClient を返す。"""
    client = MagicMock()
    # generate_structured は引数の schema を見て返すものを切り替える
    async def _generate_structured(system_prompt, user_message, schema, **kwargs):
        if schema is CharacterConceptOutput:
            return concept
        if schema is PersonaDefinition:
            return persona
        return None

    client.generate_structured = _generate_structured
    return client


# ──────────────────────────────────────
# PersonaBuilder インポート
# ──────────────────────────────────────


@pytest.fixture
def builder_with_success():
    """両方の LLM 呼び出しが成功するケース。"""
    from core.persona_builder import PersonaBuilder

    concept = _make_concept()
    persona = _make_persona()
    lm = _make_lm_client(concept, persona)
    return PersonaBuilder(lm), concept, persona


@pytest.fixture
def builder_with_concept_only():
    """ペルソナ生成のみ失敗し、デフォルトフォールバックが使われるケース。"""
    from core.persona_builder import PersonaBuilder

    concept = _make_concept()
    lm = _make_lm_client(concept, None)  # persona が None
    return PersonaBuilder(lm), concept


@pytest.fixture
def builder_all_fail():
    """コンセプト生成も失敗するケース。"""
    from core.persona_builder import PersonaBuilder

    lm = _make_lm_client(None, None)
    return PersonaBuilder(lm)


# ──────────────────────────────────────
# build_from_concept: 成功パス
# ──────────────────────────────────────


@pytest.mark.asyncio
async def test_build_from_concept_success(builder_with_success):
    builder, concept, persona = builder_with_success

    result = await builder.build_from_concept("射撃が得意な無口な少女祓魔師", player_name="Alice")

    assert result is not None
    assert result.character_name == concept.name
    assert result.system_prompt == persona.system_prompt
    assert result.persona_summary == persona.persona_summary
    assert result.speech_style_examples == persona.speech_style_examples
    assert result.portrait_keywords == concept.portrait_keywords
    assert result.concept_output is concept


@pytest.mark.asyncio
async def test_build_from_concept_success_character_json_keys(builder_with_success):
    """character_json が必要なキーをすべて持つことを確認する。"""
    builder, concept, _ = builder_with_success

    result = await builder.build_from_concept("テスト")
    assert result is not None
    cj = result.character_json

    required_keys = {"name", "alias", "hp", "sp", "body", "soul", "skill", "magic",
                     "mobility", "armor", "items", "memo", "skills", "weapons", "_persona"}
    assert required_keys.issubset(cj.keys()), f"不足キー: {required_keys - cj.keys()}"


@pytest.mark.asyncio
async def test_build_from_concept_success_stats_mapped(builder_with_success):
    """character_json のステータスが CharacterConceptOutput.initial_stats と一致するか。"""
    builder, concept, _ = builder_with_success

    result = await builder.build_from_concept("テスト")
    assert result is not None
    cj = result.character_json

    stats = concept.initial_stats
    assert cj["hp"] == stats.hp
    assert cj["body"] == stats.body
    assert cj["skill"] == stats.skill
    assert cj["magic"] == stats.magic
    assert cj["mobility"] == stats.mobility


@pytest.mark.asyncio
async def test_build_from_concept_success_skills_mapped(builder_with_success):
    """recommended_skills が character_json.skills に正しく変換されるか。"""
    builder, concept, _ = builder_with_success

    result = await builder.build_from_concept("テスト")
    assert result is not None
    skills = result.character_json["skills"]

    assert len(skills) == len(concept.recommended_skills)
    assert skills[0]["name"] == concept.recommended_skills[0].name
    assert skills[0]["cost"] == concept.recommended_skills[0].cost
    assert skills[0]["description"] == concept.recommended_skills[0].effect


@pytest.mark.asyncio
async def test_build_from_concept_success_persona_in_json(builder_with_success):
    """character_json._persona に portrait_keywords が含まれるか。"""
    builder, concept, _ = builder_with_success

    result = await builder.build_from_concept("テスト")
    assert result is not None
    persona_dict = result.character_json["_persona"]

    assert "portrait_keywords" in persona_dict
    assert persona_dict["portrait_keywords"] == concept.portrait_keywords
    assert persona_dict["speech_style"] == concept.speech_style
    assert persona_dict["motivation"] == concept.motivation


# ──────────────────────────────────────
# build_from_concept: ペルソナ失敗フォールバック
# ──────────────────────────────────────


@pytest.mark.asyncio
async def test_build_from_concept_persona_fallback(builder_with_concept_only):
    """ペルソナ LLM 生成が失敗してもデフォルトプロンプトにフォールバックする。"""
    builder, concept = builder_with_concept_only

    result = await builder.build_from_concept("テスト")

    assert result is not None
    assert result.character_name == concept.name
    # デフォルトプロンプトにはキャラクター名が含まれる
    assert concept.name in result.system_prompt
    assert concept.archetype in result.system_prompt
    # 話し方サンプルは空になる
    assert result.speech_style_examples == []


@pytest.mark.asyncio
async def test_build_from_concept_persona_fallback_has_forbidden(builder_with_concept_only):
    """フォールバックシステムプロンプトに NG 行動が含まれるか。"""
    builder, concept = builder_with_concept_only

    result = await builder.build_from_concept("テスト")
    assert result is not None

    for forbidden in concept.forbidden_actions:
        assert forbidden in result.system_prompt


# ──────────────────────────────────────
# build_from_concept: 全失敗 → None 返却
# ──────────────────────────────────────


@pytest.mark.asyncio
async def test_build_from_concept_all_fail_returns_none(builder_all_fail):
    """コンセプト生成が失敗すると None を返す。"""
    builder = builder_all_fail

    result = await builder.build_from_concept("テスト")
    assert result is None


# ──────────────────────────────────────
# build_npc_persona
# ──────────────────────────────────────


@pytest.mark.asyncio
async def test_build_npc_persona_delegates(builder_with_success):
    """build_npc_persona が build_from_concept に委譲することを確認。"""
    builder, concept, persona = builder_with_success

    result = await builder.build_npc_persona(
        npc_description="老齢の神社神主、温厚だが秘密を抱える",
        relationship_to_party="情報提供者",
    )

    assert result is not None
    assert result.character_name == concept.name


@pytest.mark.asyncio
async def test_build_npc_persona_no_relationship(builder_with_success):
    """relationship_to_party を省略してもエラーにならない。"""
    builder, concept, persona = builder_with_success

    result = await builder.build_npc_persona(npc_description="謎の商人")
    assert result is not None


# ──────────────────────────────────────
# _concept_to_character_json 直接テスト
# ──────────────────────────────────────


def test_concept_to_character_json_items_structure():
    """items フィールドが既定のアイテム名を持つ dict になるか。"""
    from core.persona_builder import PersonaBuilder

    lm = MagicMock()
    builder = PersonaBuilder(lm)
    concept = _make_concept()
    cj = builder._concept_to_character_json(concept)

    assert isinstance(cj["items"], dict)
    # 祓魔師アイテムが含まれているか
    assert "katashiro" in cj["items"]
    assert "haraegushi" in cj["items"]


def test_concept_to_character_json_memo_contains_fields():
    """memo に背景・性格・動機が含まれるか。"""
    from core.persona_builder import PersonaBuilder

    lm = MagicMock()
    builder = PersonaBuilder(lm)
    concept = _make_concept()
    cj = builder._concept_to_character_json(concept)

    assert concept.background in cj["memo"]
    assert concept.personality in cj["memo"]
    assert concept.motivation in cj["memo"]


def test_concept_to_character_json_empty_skills():
    """recommended_skills が空でも skills=[] になるか。"""
    from core.persona_builder import PersonaBuilder

    lm = MagicMock()
    builder = PersonaBuilder(lm)
    concept = _make_concept(recommended_skills=[])
    cj = builder._concept_to_character_json(concept)

    assert cj["skills"] == []


# ──────────────────────────────────────
# _default_system_prompt 直接テスト
# ──────────────────────────────────────


def test_default_system_prompt_contains_name():
    """デフォルトシステムプロンプトにキャラクター名が含まれるか。"""
    from core.persona_builder import PersonaBuilder

    lm = MagicMock()
    builder = PersonaBuilder(lm)
    concept = _make_concept()
    prompt = builder._default_system_prompt(concept)

    assert concept.name in prompt
    assert concept.archetype in prompt
    assert concept.speech_style in prompt


def test_default_system_prompt_no_forbidden_actions():
    """forbidden_actions が空の場合でもクラッシュしない。"""
    from core.persona_builder import PersonaBuilder

    lm = MagicMock()
    builder = PersonaBuilder(lm)
    concept = _make_concept(forbidden_actions=[])
    prompt = builder._default_system_prompt(concept)

    assert "なし" in prompt


# ──────────────────────────────────────
# rule_system 引数のテスト
# ──────────────────────────────────────


@pytest.mark.asyncio
async def test_build_from_concept_passes_rule_system():
    """generate_structured に rule_system がユーザーメッセージ内に渡されるか。"""
    from core.persona_builder import PersonaBuilder

    received_user_messages: list[str] = []

    async def _generate_structured(system_prompt, user_message, schema, **kwargs):
        received_user_messages.append(user_message)
        if schema is CharacterConceptOutput:
            return _make_concept()
        return _make_persona()

    lm = MagicMock()
    lm.generate_structured = _generate_structured

    builder = PersonaBuilder(lm, rule_system="custom_system")
    await builder.build_from_concept("テスト")

    # 最初の呼び出し（コンセプト生成）にルールシステムが含まれるか
    assert any("custom_system" in m for m in received_user_messages)
