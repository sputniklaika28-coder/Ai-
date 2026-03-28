"""session_copilot.py — TRPG セッション・コパイロット。

Phase 1〜3 の全機能を統合し、リアルタイムのセッション監視・
シーン遷移・イベント駆動型自動対応を提供する最上位オーケストレーター。

既存の CCFoliaConnector（チャット監視・エージェントループ）の
上層レイヤーとして動作し、シーン管理とイベントルールを追加する。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# シーン定義
# ──────────────────────────────────────────


@dataclass
class SceneDefinition:
    """ゲームシーンの定義。

    背景・BGM・キャラクター配置を一括で定義し、
    シーン遷移時に RoomBuilder で自動適用する。
    """

    name: str
    description: str = ""
    background_image: str = ""
    bgm: list[dict] = field(default_factory=list)
    characters: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> SceneDefinition:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            background_image=data.get("background_image", ""),
            bgm=data.get("bgm", []),
            characters=data.get("characters", []),
            metadata=data.get("metadata", {}),
        )

    def to_room_definition_dict(self) -> dict:
        """RoomDefinition.from_dict() 互換の dict に変換する。"""
        return {
            "name": self.name,
            "description": self.description,
            "background_image": self.background_image,
            "bgm": self.bgm,
            "characters": self.characters,
        }


# ──────────────────────────────────────────
# イベントルール
# ──────────────────────────────────────────


@dataclass
class EventRule:
    """チャットメッセージに対するイベント駆動ルール。

    パターンにマッチしたメッセージに対して、
    指定のアクションを自動実行する。
    """

    name: str
    pattern: str  # 正規表現パターン
    action: str  # アクション種別: "transition", "bgm", "narration", "spawn", "custom"
    params: dict = field(default_factory=dict)
    enabled: bool = True

    _compiled: re.Pattern | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            self._compiled = re.compile(self.pattern, re.IGNORECASE)
        except re.error:
            logger.error("無効な正規表現: %s", self.pattern)
            self._compiled = None

    def matches(self, message: str) -> re.Match | None:
        """メッセージがパターンにマッチするか判定する。"""
        if not self.enabled or self._compiled is None:
            return None
        return self._compiled.search(message)

    @classmethod
    def from_dict(cls, data: dict) -> EventRule:
        return cls(
            name=data.get("name", ""),
            pattern=data.get("pattern", ""),
            action=data.get("action", "custom"),
            params=data.get("params", {}),
            enabled=data.get("enabled", True),
        )


@dataclass
class ActionResult:
    """イベントアクションの実行結果。"""

    rule_name: str
    action: str
    success: bool
    detail: str = ""
    error: str = ""


# ──────────────────────────────────────────
# SessionCoPilot
# ──────────────────────────────────────────


class SessionCoPilot:
    """TRPG セッション・コパイロット。

    シーン管理とイベントルールを提供する。
    実際の VTT 操作は adapter / RoomBuilder に委譲する。

    2つのモード:
    - auto: イベントルールに基づき自動でアクションを実行
    - assist: アクション提案のみ（実行はしない）
    """

    def __init__(
        self,
        adapter: object | None = None,
        mode: str = "auto",
    ) -> None:
        self._adapter = adapter
        self._mode = mode  # "auto" or "assist"
        self._scenes: dict[str, SceneDefinition] = {}
        self._current_scene: str = ""
        self._event_rules: list[EventRule] = []
        self._scene_history: list[str] = []
        self._action_log: list[ActionResult] = []

    # ──────────────────────────────────────────
    # プロパティ
    # ──────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value in ("auto", "assist"):
            self._mode = value

    @property
    def current_scene(self) -> str:
        return self._current_scene

    @property
    def scene_history(self) -> list[str]:
        return list(self._scene_history)

    @property
    def action_log(self) -> list[ActionResult]:
        return list(self._action_log)

    @property
    def scenes(self) -> dict[str, SceneDefinition]:
        return dict(self._scenes)

    @property
    def event_rules(self) -> list[EventRule]:
        return list(self._event_rules)

    # ──────────────────────────────────────────
    # シーン管理
    # ──────────────────────────────────────────

    def register_scene(self, scene: SceneDefinition) -> None:
        """シーンを登録する。"""
        self._scenes[scene.name] = scene
        logger.info("シーン登録: %s", scene.name)

    def register_scenes(self, scenes: list[SceneDefinition]) -> None:
        """複数シーンを一括登録する。"""
        for scene in scenes:
            self.register_scene(scene)

    def load_scenes_from_file(self, path: str) -> int:
        """JSON ファイルからシーンを読み込む。

        Args:
            path: JSON ファイルパス。形式: {"scenes": [SceneDefinition...]}

        Returns:
            読み込んだシーン数。
        """
        import json

        p = Path(path)
        if not p.exists():
            logger.error("シーンファイルが見つかりません: %s", path)
            return 0
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            scenes_data = data if isinstance(data, list) else data.get("scenes", [])
            for s in scenes_data:
                self.register_scene(SceneDefinition.from_dict(s))
            return len(scenes_data)
        except Exception as e:
            logger.error("シーンファイル読み込みエラー: %s", e)
            return 0

    def transition_to(self, scene_name: str) -> list[dict]:
        """指定シーンに遷移する。

        RoomBuilder を使って背景・BGM・キャラクターを一括変更する。

        Args:
            scene_name: 遷移先シーン名。

        Returns:
            RoomBuilder の StepResult リスト（dict 変換済み）。
        """
        if scene_name not in self._scenes:
            logger.error("シーンが見つかりません: %s", scene_name)
            return [{"step": "transition", "success": False, "error": f"未登録シーン: {scene_name}"}]

        scene = self._scenes[scene_name]

        if self._adapter is None:
            logger.error("アダプターが未設定です")
            return [{"step": "transition", "success": False, "error": "アダプター未設定"}]

        results = self._apply_scene(scene)

        self._scene_history.append(scene_name)
        self._current_scene = scene_name
        logger.info("シーン遷移完了: %s", scene_name)
        return results

    def get_scene(self, name: str) -> SceneDefinition | None:
        """シーンを取得する。"""
        return self._scenes.get(name)

    def list_scenes(self) -> list[str]:
        """登録済みシーン名のリストを返す。"""
        return list(self._scenes.keys())

    # ──────────────────────────────────────────
    # イベントルール
    # ──────────────────────────────────────────

    def add_rule(self, rule: EventRule) -> None:
        """イベントルールを追加する。"""
        self._event_rules.append(rule)
        logger.info("ルール追加: %s (action=%s)", rule.name, rule.action)

    def add_rules(self, rules: list[EventRule]) -> None:
        """複数ルールを一括追加する。"""
        for rule in rules:
            self.add_rule(rule)

    def remove_rule(self, name: str) -> bool:
        """名前でルールを削除する。"""
        before = len(self._event_rules)
        self._event_rules = [r for r in self._event_rules if r.name != name]
        return len(self._event_rules) < before

    def process_message(self, speaker: str, body: str) -> list[ActionResult]:
        """メッセージを全ルールに照合し、マッチしたアクションを実行する。

        Args:
            speaker: 発言者名。
            body: メッセージ本文。

        Returns:
            実行されたアクションの結果リスト。
        """
        results: list[ActionResult] = []
        for rule in self._event_rules:
            match = rule.matches(body)
            if match is None:
                continue

            if self._mode == "assist":
                r = ActionResult(
                    rule_name=rule.name, action=rule.action, success=True,
                    detail=f"[提案] {rule.action}: {rule.params}",
                )
                results.append(r)
                self._action_log.append(r)
                continue

            r = self._execute_action(rule, match, speaker, body)
            results.append(r)
            self._action_log.append(r)

        return results

    # ──────────────────────────────────────────
    # アクション実行
    # ──────────────────────────────────────────

    def _execute_action(
        self, rule: EventRule, match: re.Match, speaker: str, body: str,
    ) -> ActionResult:
        """ルールに基づきアクションを実行する。"""
        try:
            if rule.action == "transition":
                scene_name = rule.params.get("scene", "")
                results = self.transition_to(scene_name)
                success = all(r.get("success", False) for r in results) if results else False
                return ActionResult(
                    rule_name=rule.name, action="transition",
                    success=success, detail=f"→ {scene_name}",
                )

            if rule.action == "bgm":
                return self._action_bgm(rule)

            if rule.action == "narration":
                return self._action_narration(rule, speaker, body)

            if rule.action == "spawn":
                return self._action_spawn(rule)

            return ActionResult(
                rule_name=rule.name, action=rule.action,
                success=False, error=f"未知のアクション: {rule.action}",
            )

        except Exception as e:
            return ActionResult(
                rule_name=rule.name, action=rule.action,
                success=False, error=str(e),
            )

    def _action_bgm(self, rule: EventRule) -> ActionResult:
        """BGM を切り替える。"""
        bgm_name = rule.params.get("bgm_name", "")
        if not bgm_name:
            return ActionResult(
                rule_name=rule.name, action="bgm",
                success=False, error="bgm_name が未指定",
            )
        try:
            result = self._adapter.switch_bgm(bgm_name)  # type: ignore[union-attr]
            success = getattr(result, "success", bool(result))
            return ActionResult(
                rule_name=rule.name, action="bgm",
                success=success, detail=bgm_name,
            )
        except Exception as e:
            return ActionResult(
                rule_name=rule.name, action="bgm",
                success=False, error=str(e),
            )

    def _action_narration(
        self, rule: EventRule, speaker: str, body: str,
    ) -> ActionResult:
        """ナレーションを送信する。"""
        text = rule.params.get("text", "")
        char_name = rule.params.get("character", "GM")
        if not text:
            return ActionResult(
                rule_name=rule.name, action="narration",
                success=False, error="text が未指定",
            )
        try:
            ok = self._adapter.send_chat(char_name, text)  # type: ignore[union-attr]
            return ActionResult(
                rule_name=rule.name, action="narration",
                success=bool(ok), detail=f"[{char_name}] {text[:40]}",
            )
        except Exception as e:
            return ActionResult(
                rule_name=rule.name, action="narration",
                success=False, error=str(e),
            )

    def _action_spawn(self, rule: EventRule) -> ActionResult:
        """キャラクターを配置する。"""
        ccfolia_data = rule.params.get("ccfolia_data", {})
        if not ccfolia_data:
            return ActionResult(
                rule_name=rule.name, action="spawn",
                success=False, error="ccfolia_data が未指定",
            )
        try:
            ok = self._adapter.spawn_piece(ccfolia_data)  # type: ignore[union-attr]
            return ActionResult(
                rule_name=rule.name, action="spawn",
                success=bool(ok),
                detail=ccfolia_data.get("name", "unknown"),
            )
        except Exception as e:
            return ActionResult(
                rule_name=rule.name, action="spawn",
                success=False, error=str(e),
            )

    # ──────────────────────────────────────────
    # シーン適用（内部）
    # ──────────────────────────────────────────

    def _apply_scene(self, scene: SceneDefinition) -> list[dict]:
        """シーン定義を RoomBuilder 経由で適用する。"""
        try:
            from core.room_builder import CharacterPlacement, RoomBuilder
        except ModuleNotFoundError:
            from room_builder import CharacterPlacement, RoomBuilder  # type: ignore[no-redef]

        builder = RoomBuilder(adapter=self._adapter)

        # シーン遷移ではルーム新規作成はスキップし、アセット変更のみ実行
        results: list[dict] = []

        if scene.background_image:
            r = builder.set_background(scene.background_image)
            results.append({"step": r.step, "success": r.success, "detail": r.detail, "error": r.error})

        for bgm in scene.bgm:
            r = builder.add_bgm(bgm.get("file_path", ""), bgm.get("name", ""))
            results.append({"step": r.step, "success": r.success, "detail": r.detail, "error": r.error})

        for char_data in scene.characters:
            char = CharacterPlacement.from_dict(char_data)
            r = builder.place_character(char)
            results.append({"step": r.step, "success": r.success, "detail": r.detail, "error": r.error})

        if not results:
            results.append({"step": "transition", "success": True, "detail": scene.name})

        return results
