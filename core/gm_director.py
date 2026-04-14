"""gm_director.py — GM ターン処理の統合オーケストレーター (Phase 4)。

プレイヤーのチャットメッセージを受け取り、以下を一貫して処理する:

  1. 行動意図の解釈  — generate_structured(GameIntention)
  2. 戦闘解決        — CombatEngine.resolve() → 決定論的結果
  3. コンテキスト構築 — GameState.summary() + EntityTracker.context_summary()
  4. ナレーション生成 — lm_client.generate_response() with full context
  5. エンティティ抽出 — generate_structured(EntityExtractionResult)
  6. エンティティ更新 — EntityTracker.upsert()

これにより「毎ターン HP を LLM に脳内計算させる」問題と
「NPC の名前を数セッション後に忘れる」問題を同時に解決する。

使用例::
    director = GMDirector(lm_client, game_state, entity_tracker)
    result = await director.process_turn("ゴブリンに攻撃する", "アリス")
    print(result.narration)
    print(result.combat_result)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.entity_tracker import Entity, EntityTracker
from core.game_state import GameState
from core.schemas import (
    CombatActionResult,
    EntityExtractionResult,
    GameIntention,
    GMTurnResultSchema,
    SessionContext,
)

if TYPE_CHECKING:
    from core.combat_engine import CombatEngine
    from core.lm_client import LMClient

logger = logging.getLogger(__name__)

# ──────────────────────────────────────
# 定数・プロンプトテンプレート
# ──────────────────────────────────────

_INTENTION_SYSTEM_PROMPT = """\
あなたはTRPG GMの行動意図パーサーです。
プレイヤーのメッセージから「誰が何をするか」を解釈してください。

action_type の選択肢:
- attack: 敵への物理・霊的攻撃
- skill: スキルの使用
- item: アイテムの使用・消費
- move: 移動・位置変更
- dialogue: NPC への発言・交渉・情報収集
- other: その他（探索・調査・待機など）

不明な場合は action_type="other" を使用してください。
"""

_NARRATION_SYSTEM_PROMPT_TEMPLATE = """\
あなたはタクティカル祓魔師TRPGのゲームマスターAIです。
プレイヤーの行動に対して、世界観に沿った臨場感のあるナレーションを生成してください。

【GM ガイドライン】
- 一人称視点ではなく、三人称の客観的な語り口を使う
- ダイスロール結果（成功・失敗・クリティカル）は具体的に描写する
- HP が 0 になった場合は確実に倒れた演出をする
- 戦闘外では探索・謎解き・対話の緊張感を演出する
- 日本語で 150〜300 文字程度のナレーション

{context_block}
"""

_ENTITY_EXTRACTION_PROMPT = """\
以下のナレーションテキストから、セッション追跡が必要なエンティティを抽出してください。

抽出対象:
- npc: 固有名詞を持つ NPC・敵キャラクター
- item: 固有名を持つ武器・道具・アーティファクト
- location: 場所・建物・地名
- quest_flag: クエスト目標・重要な出来事・フラグ

既出エンティティの状態変化（撃破・消耗など）も反映させてください。
新しいエンティティがない場合は空リストを返してください。
"""

# ──────────────────────────────────────
# 結果データクラス
# ──────────────────────────────────────


@dataclass
class GMTurnResult:
    """GMDirector.process_turn() の戻り値。"""

    intention: GameIntention | None
    """解釈されたプレイヤー行動意図。"""

    combat_result: CombatActionResult | None
    """CombatEngine による戦闘解決結果（非戦闘アクションは None）。"""

    narration: str
    """LLM が生成したGMナレーション本文。"""

    vtt_chat_lines: list[str] = field(default_factory=list)
    """VTT チャットに投稿する行リスト（ナレーション + HP 変化通知等）。"""

    new_entities: list[Entity] = field(default_factory=list)
    """このターンで新規登録・更新されたエンティティ。"""

    context_injected: str = ""
    """LLM に注入したコンテキストブロック（デバッグ用）。"""

    error: str | None = None
    """処理エラーメッセージ（正常時は None）。"""

    def to_schema(self) -> GMTurnResultSchema:
        return GMTurnResultSchema(
            intention_type=self.intention.action_type if self.intention else "",
            combat_resolved=self.combat_result is not None,
            narration=self.narration,
            vtt_chat_lines=self.vtt_chat_lines,
            new_entity_names=[e.name for e in self.new_entities],
            error=self.error,
        )


# ──────────────────────────────────────
# GMDirectorConfig
# ──────────────────────────────────────


@dataclass
class GMDirectorConfig:
    """GMDirector の動作設定。"""

    auto_resolve_combat: bool = True
    """True の場合、戦闘アクションを CombatEngine で自動解決する。"""

    inject_game_state: bool = True
    """True の場合、ナレーション生成プロンプトに GameState サマリーを注入する。"""

    inject_entities: bool = True
    """True の場合、ナレーション生成プロンプトに EntityTracker サマリーを注入する。"""

    auto_extract_entities: bool = True
    """True の場合、ナレーション後にエンティティを自動抽出・登録する。"""

    max_recent_events: int = 5
    """コンテキストに含める直近イベント数。"""

    narration_temperature: float = 0.7
    """ナレーション生成の temperature。"""

    narration_max_tokens: int = 400
    """ナレーション生成の最大トークン数。"""


# ──────────────────────────────────────
# GMDirector 本体
# ──────────────────────────────────────


class GMDirector:
    """GM ターン処理を統合するオーケストレーター。

    LMClient・GameState・EntityTracker・CombatEngine を協調させ、
    プレイヤーの 1 ターンを完全に処理する。

    Args:
        lm_client: LMClient インスタンス。
        game_state: セッションの GameState。
        entity_tracker: セッションの EntityTracker。
        combat_engine: CombatEngine（省略時は GameState から自動生成）。
        config: 動作設定。
    """

    def __init__(
        self,
        lm_client: "LMClient",
        game_state: GameState,
        entity_tracker: EntityTracker,
        combat_engine: "CombatEngine | None" = None,
        config: GMDirectorConfig | None = None,
    ) -> None:
        self._lm = lm_client
        self._state = game_state
        self._entities = entity_tracker
        self._config = config or GMDirectorConfig()
        self._recent_events: list[str] = []

        # CombatEngine は遅延初期化（GameState は既に存在する）
        if combat_engine is not None:
            self._engine: "CombatEngine | None" = combat_engine
        else:
            self._engine = None

    @property
    def _combat_engine(self) -> "CombatEngine":
        """CombatEngine を遅延初期化する。"""
        if self._engine is None:
            from core.combat_engine import CombatEngine
            self._engine = CombatEngine(self._state)
        return self._engine

    # ──────────────────────────────────
    # メイン API
    # ──────────────────────────────────

    async def process_turn(
        self,
        player_message: str,
        character_name: str = "",
        extra_context: str = "",
    ) -> GMTurnResult:
        """プレイヤーの 1 ターンを処理して GMTurnResult を返す。

        Args:
            player_message: プレイヤーのチャットメッセージ。
            character_name: プレイヤーキャラクター名（省略時はメッセージから推定）。
            extra_context: 追加コンテキスト（任意）。

        Returns:
            GMTurnResult（narration・combat_result・new_entities 等を含む）。
        """
        logger.info(
            "GMDirector: ターン処理開始 [%s] '%s'",
            character_name or "?",
            player_message[:40],
        )

        # Step 1: 行動意図の解釈
        intention = await self._parse_intention(player_message, character_name)

        # Step 2: 戦闘解決（combat フェーズかつ auto_resolve が有効な場合）
        combat_result: CombatActionResult | None = None
        if (
            self._config.auto_resolve_combat
            and intention is not None
            and intention.action_type in ("attack", "skill", "item")
            and self._state.phase == "combat"
        ):
            combat_result = self._combat_engine.resolve(intention)
            logger.info(
                "GMDirector: 戦闘解決 [%s] success=%s damage=%d",
                intention.action_type,
                combat_result.success,
                combat_result.damage_dealt,
            )

        # Step 3: コンテキストブロック構築
        context_block = self.build_context_block(extra_context)

        # Step 4: ナレーション生成
        narration = await self._generate_narration(
            player_message=player_message,
            intention=intention,
            combat_result=combat_result,
            context_block=context_block,
        )

        # Step 5: エンティティ自動抽出
        new_entities: list[Entity] = []
        if self._config.auto_extract_entities and narration:
            new_entities = await self._extract_entities(
                narration,
                round_number=self._state.round_number,
            )

        # Step 6: 直近イベントを記録
        event_summary = self._build_event_summary(intention, combat_result, narration)
        self._recent_events.append(event_summary)
        if len(self._recent_events) > self._config.max_recent_events:
            self._recent_events.pop(0)

        # VTT チャット行を生成
        vtt_lines = self._build_vtt_lines(narration, combat_result)

        return GMTurnResult(
            intention=intention,
            combat_result=combat_result,
            narration=narration,
            vtt_chat_lines=vtt_lines,
            new_entities=new_entities,
            context_injected=context_block,
        )

    # ──────────────────────────────────
    # コンテキスト構築
    # ──────────────────────────────────

    def build_context_block(self, extra: str = "") -> str:
        """LLM プロンプトに注入するコンテキストブロックを構築する。"""
        parts: list[str] = []

        if self._config.inject_game_state:
            parts.append(self._state.summary())

        if self._config.inject_entities:
            entity_summary = self._entities.context_summary(max_per_type=4)
            if entity_summary:
                parts.append(entity_summary)

        if self._recent_events:
            parts.append("【直近のイベント】")
            parts.extend(f"  ・{e}" for e in self._recent_events[-self._config.max_recent_events:])

        if extra:
            parts.append(extra)

        return "\n\n".join(parts)

    def get_session_context(self) -> SessionContext:
        """現在のセッションコンテキストを SessionContext スキーマで返す。"""
        return SessionContext(
            game_state_summary=self._state.summary(),
            entity_summary=self._entities.context_summary(),
            recent_events=list(self._recent_events),
            round_number=self._state.round_number,
            phase=self._state.phase,
        )

    # ──────────────────────────────────
    # 内部メソッド
    # ──────────────────────────────────

    async def _parse_intention(
        self,
        player_message: str,
        character_name: str,
    ) -> GameIntention | None:
        """プレイヤーメッセージを GameIntention に変換する。"""
        actor = character_name or "プレイヤー"
        user_msg = f"キャラクター名: {actor}\nメッセージ: {player_message}"

        try:
            result = await self._lm.generate_structured(
                system_prompt=_INTENTION_SYSTEM_PROMPT,
                user_message=user_msg,
                schema=GameIntention,
                temperature=0.2,
                max_tokens=200,
            )
            if result is None:
                return GameIntention(actor=actor, action_type="other")
            if not result.actor:
                result.actor = actor
            return result
        except Exception as e:
            logger.warning("GMDirector: 意図解釈失敗: %s", e)
            return GameIntention(actor=actor, action_type="other")

    async def _generate_narration(
        self,
        player_message: str,
        intention: GameIntention | None,
        combat_result: CombatActionResult | None,
        context_block: str,
    ) -> str:
        """ナレーションを生成する。"""
        system_prompt = _NARRATION_SYSTEM_PROMPT_TEMPLATE.format(
            context_block=context_block if context_block else "（コンテキストなし）"
        )

        # ユーザーメッセージにアクション情報を追加
        parts = [f"プレイヤーの行動: {player_message}"]
        if intention:
            parts.append(f"行動種別: {intention.action_type}")
        if combat_result:
            parts.append(f"戦闘解決結果: {combat_result.narration_hint}")
            if combat_result.roll:
                parts.append(f"ダイス結果: {combat_result.roll.narration}")

        user_msg = "\n".join(parts)

        try:
            response, _ = await self._lm.generate_response(
                system_prompt=system_prompt,
                user_message=user_msg,
                temperature=self._config.narration_temperature,
                max_tokens=self._config.narration_max_tokens,
            )
            return response or "（ナレーション生成失敗）"
        except Exception as e:
            logger.error("GMDirector: ナレーション生成エラー: %s", e)
            # フォールバック: 戦闘結果のみ返す
            if combat_result:
                return combat_result.narration_hint
            return f"（エラー: {e}）"

    async def _extract_entities(
        self,
        narration: str,
        round_number: int = 0,
    ) -> list[Entity]:
        """ナレーションからエンティティを抽出して EntityTracker に登録する。"""
        try:
            result = await self._lm.generate_structured(
                system_prompt=_ENTITY_EXTRACTION_PROMPT,
                user_message=f"ナレーションテキスト:\n{narration}",
                schema=EntityExtractionResult,
                temperature=0.1,
                max_tokens=300,
            )
            if result is None or not result.entities:
                return []
            return self._entities.from_schema_list(result.entities, round_number)
        except Exception as e:
            logger.warning("GMDirector: エンティティ抽出失敗: %s", e)
            return []

    def _build_event_summary(
        self,
        intention: GameIntention | None,
        combat_result: CombatActionResult | None,
        narration: str,
    ) -> str:
        """直近イベントリスト用の短いサマリー文を作る。"""
        if combat_result and combat_result.success:
            return (
                f"R{self._state.round_number}: "
                f"{combat_result.actor} → {combat_result.target or '?'} "
                f"[{combat_result.action_type}] "
                f"ダメージ {combat_result.damage_dealt}"
            )
        if intention:
            return (
                f"R{self._state.round_number}: "
                f"{intention.actor} [{intention.action_type}]"
            )
        return f"R{self._state.round_number}: {narration[:40]}…"

    def _build_vtt_lines(
        self,
        narration: str,
        combat_result: CombatActionResult | None,
    ) -> list[str]:
        """VTT チャットに投稿するテキスト行のリストを返す。"""
        lines: list[str] = []

        if narration:
            lines.append(narration)

        if combat_result and combat_result.hp_after is not None:
            lines.append(
                f"（{combat_result.target}: 残 HP {combat_result.hp_after}）"
            )

        return lines
