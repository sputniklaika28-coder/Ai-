"""persona_builder.py — コンセプトテキストからキャラクターJSON + システムプロンプトを自動生成。

Phase 2 実装: プレイヤーの簡単な入力から AI が最適な口調・行動原理・NG 行動などを
定義した「キャラクター JSON」および「システムプロンプト」を生成する。

使用例::
    builder = PersonaBuilder(lm_client)
    result = await builder.build_from_concept("射撃戦が得意な無口な少女祓魔師")
    if result:
        print(result.system_prompt)
        save_character(result.character_name, result.character_json)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.schemas import CharacterConceptOutput, PersonaDefinition

if TYPE_CHECKING:
    from core.lm_client import LMClient

logger = logging.getLogger(__name__)

# ──────────────────────────────────────
# 定数・プロンプトテンプレート
# ──────────────────────────────────────

_CONCEPT_SYSTEM_PROMPT = """\
あなたはTRPGキャラクタージェネレーターです。
プレイヤーのコンセプトテキストから、ゲームで使えるキャラクター定義を生成してください。

ルール:
- name: 日本語の固有名詞（漢字またはカタカナ）
- archetype: 「重戦士」「回復役」「斥候」「魔法使い」「支援型」などの役割
- portrait_keywords: 英語で外見を描写するキーワードリスト（画像生成用）
- initial_stats の合計値は 20〜25 の範囲に収めてください
- recommended_skills は 2〜4 個を目安に、コンセプトに合ったものを考案してください
"""

_PERSONA_SYSTEM_PROMPT = """\
あなたはTRPG用キャラクターのペルソナ設計者です。
キャラクター情報からAIに渡す「システムプロンプト」と「ペルソナ定義」を生成してください。

システムプロンプトの要件:
- キャラクターとして一人称で話すよう指示する
- 話し方・語尾・口癖を具体的に指定する
- NG行動を明確に列挙する
- 動機・信念を反映させる
- 必ず日本語で記述する（200〜400文字目安）

speech_style_examples: 実際のセリフ例を3〜5文
forbidden_topics: 絶対に応答しないトピックのリスト
"""


# ──────────────────────────────────────
# 結果データクラス
# ──────────────────────────────────────

@dataclass
class PersonaBuildResult:
    """PersonaBuilder.build_from_concept() の最終結果。"""

    character_name: str
    """キャラクター名。"""

    character_json: dict
    """VTT・char_maker.py と互換性のあるキャラクターシート dict。"""

    system_prompt: str
    """LMClient.generate_response() の system_prompt に渡せる文字列。"""

    persona_summary: str = ""
    """GUI 表示用の1行サマリー。"""

    speech_style_examples: list[str] = field(default_factory=list)
    """話し方サンプル（プレビュー用）。"""

    portrait_keywords: list[str] = field(default_factory=list)
    """立ち絵生成用英語キーワード（PortraitPipeline に渡す）。"""

    concept_output: CharacterConceptOutput | None = None
    """中間出力（CharacterConceptOutput）。デバッグ・再利用用。"""


# ──────────────────────────────────────
# PersonaBuilder 本体
# ──────────────────────────────────────

class PersonaBuilder:
    """コンセプトテキストからキャラクターJSON + システムプロンプトを生成する。

    `generate_structured()` を利用してモデルレベルで JSON 整合性を保証する。
    regex や手動パースは一切使用しない。

    Args:
        lm_client: LMClient インスタンス（generate_structured が使えること）。
        rule_system: 対象ルールシステム名（プロンプト調整に使用）。
    """

    def __init__(self, lm_client: "LMClient", rule_system: str = "tactical_exorcist") -> None:
        self._lm = lm_client
        self._rule_system = rule_system

    # ──────────────────────────────────────
    # パブリック API
    # ──────────────────────────────────────

    async def build_from_concept(
        self,
        concept_text: str,
        player_name: str = "",
    ) -> PersonaBuildResult | None:
        """プレイヤーキャラクターのコンセプトテキストからペルソナを構築する。

        Args:
            concept_text: 例「射撃が得意な無口な少女祓魔師」。
            player_name: プレイヤー名（ログ用）。

        Returns:
            PersonaBuildResult、または生成失敗時 None。
        """
        logger.info("PersonaBuilder: PC生成開始 [%s] concept='%s'", player_name or "?", concept_text[:40])

        # Step 1: コンセプト → CharacterConceptOutput
        concept = await self._generate_concept(concept_text)
        if concept is None:
            logger.warning("PersonaBuilder: キャラクターコンセプト生成失敗")
            return None

        # Step 2: CharacterConceptOutput → キャラクターシート JSON
        char_json = self._concept_to_character_json(concept)

        # Step 3: CharacterConceptOutput → PersonaDefinition（システムプロンプト）
        persona = await self._generate_persona(concept, concept_text)
        if persona is None:
            # システムプロンプト生成失敗時はデフォルトにフォールバック
            logger.warning("PersonaBuilder: ペルソナ生成失敗 → デフォルトにフォールバック")
            system_prompt = self._default_system_prompt(concept)
            speech_examples: list[str] = []
            persona_summary = f"{concept.name}（{concept.archetype}）"
        else:
            system_prompt = persona.system_prompt
            speech_examples = persona.speech_style_examples
            persona_summary = persona.persona_summary or f"{concept.name}（{concept.archetype}）"

        logger.info("PersonaBuilder: PC '%s' 生成完了", concept.name)
        return PersonaBuildResult(
            character_name=concept.name,
            character_json=char_json,
            system_prompt=system_prompt,
            persona_summary=persona_summary,
            speech_style_examples=speech_examples,
            portrait_keywords=concept.portrait_keywords,
            concept_output=concept,
        )

    async def build_npc_persona(
        self,
        npc_description: str,
        relationship_to_party: str = "",
    ) -> PersonaBuildResult | None:
        """NPC のペルソナを構築する（プレイヤーキャラクター向けより簡略化）。

        Args:
            npc_description: NPC の説明（例「老齢の神社神主、温厚だが秘密を抱える」）。
            relationship_to_party: パーティとの関係（例「情報提供者」）。

        Returns:
            PersonaBuildResult、または失敗時 None。
        """
        context = npc_description
        if relationship_to_party:
            context = f"{npc_description}（パーティとの関係: {relationship_to_party}）"

        return await self.build_from_concept(concept_text=context)

    # ──────────────────────────────────────
    # 内部メソッド: LLM 呼び出し
    # ──────────────────────────────────────

    async def _generate_concept(self, concept_text: str) -> CharacterConceptOutput | None:
        """コンセプトテキストから CharacterConceptOutput を生成する。"""
        user_message = (
            f"ルールシステム: {self._rule_system}\n"
            f"プレイヤーのコンセプト: {concept_text}\n\n"
            "上記のコンセプトに基づいてキャラクター定義を生成してください。"
        )
        return await self._lm.generate_structured(
            system_prompt=_CONCEPT_SYSTEM_PROMPT,
            user_message=user_message,
            schema=CharacterConceptOutput,
            temperature=0.7,
            max_tokens=1000,
        )

    async def _generate_persona(
        self,
        concept: CharacterConceptOutput,
        original_concept: str,
    ) -> PersonaDefinition | None:
        """CharacterConceptOutput から PersonaDefinition を生成する。"""
        char_summary = (
            f"名前: {concept.name}\n"
            f"役割: {concept.archetype}\n"
            f"背景: {concept.background}\n"
            f"性格: {concept.personality}\n"
            f"話し方: {concept.speech_style}\n"
            f"動機: {concept.motivation}\n"
            f"NG行動: {', '.join(concept.forbidden_actions) if concept.forbidden_actions else 'なし'}\n"
            f"元のコンセプト: {original_concept}"
        )
        return await self._lm.generate_structured(
            system_prompt=_PERSONA_SYSTEM_PROMPT,
            user_message=f"以下のキャラクター情報からペルソナを定義してください:\n\n{char_summary}",
            schema=PersonaDefinition,
            temperature=0.5,
            max_tokens=800,
        )

    # ──────────────────────────────────────
    # 内部メソッド: データ変換
    # ──────────────────────────────────────

    def _concept_to_character_json(self, concept: CharacterConceptOutput) -> dict:
        """CharacterConceptOutput を tactical_exorcist 互換のキャラシート dict に変換する。

        出力フォーマットは addons/tactical_exorcist/char_maker.py および
        configs/saved_pcs/ に保存されるキャラシート JSON と互換。
        """
        stats = concept.initial_stats

        # スキルリストを変換（SkillEntry → dict）
        skills = [
            {
                "name": sk.name,
                "cost": sk.cost,
                "condition": sk.condition,
                "description": sk.effect,
            }
            for sk in concept.recommended_skills
        ]

        return {
            "name": concept.name,
            "alias": concept.archetype,
            "hp": stats.hp,
            "sp": stats.sp,
            "evasion": 0,
            "mobility": stats.mobility,
            "armor": stats.armor,
            "body": stats.body,
            "soul": stats.soul,
            "skill": stats.skill,
            "magic": stats.magic,
            "items": {
                "katashiro": 0,
                "haraegushi": 0,
                "shimenawa": 0,
                "juryudan": 0,
                "ireikigu": 0,
                "meifuku": 0,
                "jutsuyen": 0,
            },
            "memo": (
                f"【背景】{concept.background}\n"
                f"【性格】{concept.personality}\n"
                f"【動機】{concept.motivation}"
            ),
            "skills": skills,
            "weapons": [],
            # Phase 2 拡張フィールド（VTT アップロード時に使用）
            "_persona": {
                "speech_style": concept.speech_style,
                "motivation": concept.motivation,
                "forbidden_actions": concept.forbidden_actions,
                "portrait_keywords": concept.portrait_keywords,
                "appearance": concept.appearance,
            },
        }

    def _default_system_prompt(self, concept: CharacterConceptOutput) -> str:
        """LLM 失敗時のテンプレートベースフォールバックシステムプロンプト。"""
        forbidden = "、".join(concept.forbidden_actions) if concept.forbidden_actions else "なし"
        return (
            f"あなたは {concept.name}（{concept.archetype}）として振る舞ってください。\n\n"
            f"【性格】{concept.personality}\n"
            f"【話し方】{concept.speech_style}\n"
            f"【動機】{concept.motivation}\n"
            f"【NG行動】{forbidden}\n\n"
            "上記のキャラクターとして一人称で応答してください。"
            "キャラクターから逸脱する行動は取らないでください。"
        )
