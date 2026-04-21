"""session_config.py — 統合セッション設定スキーマ。

シナリオ概要・あらすじ・PCスキル・セッション設定・現在のステータス・
シナリオ進行・ハウスルール・追加のミニゲームを単一の Pydantic モデルに集約する。

`SessionOrchestrator` がこのモデルを読み込み、実行時に基底設定とセッション固有
設定をマージする。セッション固有の house_rules / mini_games が常に基底設定より
優先される（"その他の設定より優先する事" の要件）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PCSkill(BaseModel):
    """PCが持つスキル1件。"""

    character: str = Field(default="", description="所有キャラクター名")
    name: str = Field(..., description="スキル名")
    value: int = Field(default=0, description="技能値・成功率など")
    memo: str = Field(default="", description="補足メモ")


class HouseRule(BaseModel):
    """卓ごとに上書き可能なハウスルール。

    `priority` が高いものが優先され、同名ルールは置き換えられる。
    セッション固有のルールは基底に対し +1000 の下駄を履かせて常に勝つ。
    """

    name: str = Field(..., description="ルール名（同名は上書き対象）")
    description: str = Field(default="", description="ルール本文")
    enabled: bool = Field(default=True)
    priority: int = Field(default=100, description="高いほど優先")
    params: dict[str, Any] = Field(default_factory=dict)


class MiniGame(BaseModel):
    """セッション中に呼び出し可能な追加ミニゲーム。"""

    name: str = Field(..., description="ミニゲーム識別子")
    description: str = Field(default="")
    trigger: str = Field(default="", description="起動条件（自由記述 or 正規表現）")
    enabled: bool = Field(default=True)
    priority: int = Field(default=100)
    params: dict[str, Any] = Field(default_factory=dict)


class SessionStatus(BaseModel):
    """セッション開始時点 / 進行中の現在のステータス。"""

    scene: str = Field(default="", description="現在のシーン名")
    turn: int = Field(default=0)
    phase: str = Field(default="", description="現在のフェイズ")
    notes: str = Field(default="")
    custom: dict[str, Any] = Field(default_factory=dict)


class ScenarioProgress(BaseModel):
    """シナリオ進行の状態。GMDirector 等が更新する。"""

    current_chapter: str = Field(default="")
    completed_scenes: list[str] = Field(default_factory=list)
    pending_events: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)


class SessionConfig(BaseModel):
    """1セッション分の統合設定。

    すべてのフィールドが省略可能で、`SessionOrchestrator` が
    base_config と active_config をマージする際の入れ物として使う。
    """

    session_name: str = Field(default="")
    scenario_overview: str = Field(default="", description="シナリオ概要")
    scenario_synopsis: str = Field(default="", description="あらすじ")
    scenario_progress_notes: str = Field(default="", description="シナリオ進行メモ（自由記述）")
    pc_skills: list[PCSkill] = Field(default_factory=list)
    pc_status_notes: str = Field(default="", description="現在のステータス（自由記述）")
    gm_instructions: str = Field(default="", description="GMへの追加指示")
    settings: dict[str, Any] = Field(default_factory=dict, description="自由記述のセッション設定")
    status: SessionStatus = Field(default_factory=SessionStatus)
    progress: ScenarioProgress = Field(default_factory=ScenarioProgress)
    house_rules: list[HouseRule] = Field(default_factory=list)
    mini_games: list[MiniGame] = Field(default_factory=list)
    history_ref: str | None = Field(
        default=None,
        description="関連するセッション履歴フォルダ名（参照のみ・履歴本体は SessionManager 側）",
    )


# ──────────────────────────────────────────
# マージユーティリティ
# ──────────────────────────────────────────


def merge_rules(base: list[HouseRule], override: list[HouseRule]) -> list[HouseRule]:
    """ハウスルールをマージする。

    同名ルールは override が常に勝つ（priority に下駄を履かせる）。
    結果は priority 降順でソートされる。
    """
    by_name: dict[str, HouseRule] = {r.name: r.model_copy() for r in base}
    for r in override:
        bumped = r.model_copy(update={"priority": r.priority + 1000})
        by_name[r.name] = bumped
    return sorted(by_name.values(), key=lambda r: r.priority, reverse=True)


def merge_mini_games(base: list[MiniGame], override: list[MiniGame]) -> list[MiniGame]:
    """ミニゲームを同様にマージする。"""
    by_name: dict[str, MiniGame] = {g.name: g.model_copy() for g in base}
    for g in override:
        bumped = g.model_copy(update={"priority": g.priority + 1000})
        by_name[g.name] = bumped
    return sorted(by_name.values(), key=lambda g: g.priority, reverse=True)
