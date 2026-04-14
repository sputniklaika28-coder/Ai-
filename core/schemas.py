"""schemas.py — LLM 構造化出力用 Pydantic v2 スキーマ定義。

`LMClient.generate_structured()` に渡すことで、モデルレベルで
100% パース可能な JSON を強制する。正規表現・ブルートフォース探索不要。

使用例::
    result = await lm_client.generate_structured(
        system_prompt="...",
        user_message="...",
        schema=ChatPostAction,
    )
    if result:
        await adapter.send_chat(result.character_name, result.text)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ──────────────────────────────────────
# VTT チャット操作
# ──────────────────────────────────────


class ChatPostAction(BaseModel):
    """チャット投稿アクション。GM が NPC として発言する際に使用。"""

    character_name: str = Field(description="発言するキャラクター名")
    text: str = Field(description="投稿するチャットテキスト")


class ChatMessage(BaseModel):
    """VTT チャットログの 1 エントリ。"""

    speaker: str = Field(description="発言者名")
    body: str = Field(description="メッセージ本文")


class ChatLogResult(BaseModel):
    """VLM がスクリーンショットから抽出したチャット履歴。"""

    messages: list[ChatMessage] = Field(
        default_factory=list,
        description="画面上に見えるチャットメッセージのリスト（古い順）",
    )


# ──────────────────────────────────────
# ゲームアクション意図
# ──────────────────────────────────────

ACTION_TYPES = ("attack", "skill", "item", "move", "dialogue", "other")


class GameIntention(BaseModel):
    """LLM が解釈したプレイヤーの行動意図。ルールエンジンへの入力に使用。"""

    actor: str = Field(description="行動するキャラクター名")
    target: str | None = Field(default=None, description="対象キャラクター名（不要な場合は null）")
    action_type: str = Field(
        description=f"行動種別: {', '.join(ACTION_TYPES)} のいずれか"
    )
    skill_name: str | None = Field(default=None, description="使用するスキル名")
    item_name: str | None = Field(default=None, description="使用するアイテム名")
    dialogue: str | None = Field(default=None, description="台詞（dialogue アクションの場合）")
    notes: str | None = Field(default=None, description="補足情報・GM への注釈")


# ──────────────────────────────────────
# 記憶・要約
# ──────────────────────────────────────


class MemorySummary(BaseModel):
    """MemoryManager の圧縮要約出力。rolling summary の置き換え用。"""

    summary: str = Field(description="これまでのセッションの要約（日本語）")
    key_events: list[str] = Field(
        default_factory=list,
        description="重要イベントのリスト（箇条書き相当）",
    )
    active_characters: list[str] = Field(
        default_factory=list,
        description="現在登場中のキャラクター名リスト",
    )


# ──────────────────────────────────────
# ボード状態・VLM 座標認識
# ──────────────────────────────────────


class PieceLocation(BaseModel):
    """VLM がボードスクリーンショットから特定した駒 1 個の情報。"""

    description: str = Field(description="駒の説明（キャラクター名・外見など）")
    px_x: int = Field(description="ピクセル X 座標（スクリーンショット内、左端が 0）")
    px_y: int = Field(description="ピクセル Y 座標（スクリーンショット内、上端が 0）")
    confidence: float = Field(default=1.0, description="信頼度スコア 0.0〜1.0")


class BoardAnalysisResult(BaseModel):
    """VLM がスクリーンショットから解析したボード全体の状態。"""

    pieces: list[PieceLocation] = Field(
        default_factory=list,
        description="ボード上に存在する全駒の情報",
    )
    suggested_moves: list[dict] = Field(
        default_factory=list,
        description="推奨移動案: [{description, to_grid_x, to_grid_y, reason}]",
    )


class SingleCoordinate(BaseModel):
    """VLM がスクリーンショットから特定した単一ピクセル座標。"""

    px_x: int = Field(description="ピクセル X 座標（画面左端が 0）")
    px_y: int = Field(description="ピクセル Y 座標（画面上端が 0）")
    found: bool = Field(default=True, description="要素が見つかったか")


class VisionCoordinate(BaseModel):
    """信頼スコアとラベル付きの単一座標（VisionCoordinateList の要素）。"""

    px_x: int = Field(description="ピクセル X 座標（画面左端が 0）")
    px_y: int = Field(description="ピクセル Y 座標（画面上端が 0）")
    confidence: float = Field(default=1.0, description="信頼スコア 0.0〜1.0")
    label: str = Field(default="", description="認識した UI 要素のラベル")


class VisionCoordinateList(BaseModel):
    """VLM が特定した複数ピクセル座標（ボタン群など）。"""

    items: list[VisionCoordinate] = Field(default_factory=list)


# ──────────────────────────────────────
# GM ナレーション（複合アクション）
# ──────────────────────────────────────


class NarrativeAction(BaseModel):
    """GM の一手番アクション（語り・チャット・移動の複合）。"""

    narration: str = Field(description="GM が語るシーン描写テキスト")
    chat_speaker: str = Field(description="チャット送信キャラクター名")
    chat_text: str = Field(description="チャットに送信するテキスト")
    move_piece_id: str | None = Field(default=None, description="移動する駒 ID")
    move_grid_x: int | None = Field(default=None, description="移動先グリッド X")
    move_grid_y: int | None = Field(default=None, description="移動先グリッド Y")


# ──────────────────────────────────────
# VisionVTT アクションプラン
# ──────────────────────────────────────


class VTTActionPlan(BaseModel):
    """VisionVTT アダプターが次に実行すべき操作の決定。"""

    action: Literal["click", "drag", "type", "done", "fail"] = Field(
        description="実行する操作の種別"
    )
    target_description: str = Field(default="", description="クリック/ドラッグ対象の説明")
    px_x: int | None = Field(default=None, description="操作先 X 座標")
    px_y: int | None = Field(default=None, description="操作先 Y 座標")
    drag_to_x: int | None = Field(default=None, description="ドラッグ先 X（drag 時のみ）")
    drag_to_y: int | None = Field(default=None, description="ドラッグ先 Y（drag 時のみ）")
    text_input: str | None = Field(default=None, description="入力テキスト（type 時のみ）")
    reason: str = Field(default="", description="この操作を選んだ理由")
