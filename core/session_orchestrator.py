"""session_orchestrator.py — 統合セッション管理オーケストレーター。

シナリオ概要・あらすじ・PCスキル・セッション設定・セッション履歴・現在のステータス・
シナリオ進行を一つの窓口に集約し、セッションを「事前準備 → 開始 → 進行 → 終了」の
ライフサイクルで扱えるようにする。

主な責務:
  - 基底設定 (configs/session_config.json) の読み書き
  - セッションごとに上書き可能な設定（ハウスルール・追加のミニゲーム）の管理
  - core/session_manager.SessionManager との連携によるログ・履歴保存
  - セッション無し（active_config=None）でも動作する

優先順位:
  ハウスルール / 追加のミニゲームは「セッションごと > 基底設定」が必ず保たれる。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .session_config import (
    HouseRule,
    MiniGame,
    SessionConfig,
    merge_mini_games,
    merge_rules,
)

logger = logging.getLogger(__name__)


SESSION_CONFIG_FILENAME = "session_config.json"


class SessionOrchestrator:
    """セッション情報の事前準備・進行を統合管理する。

    `SessionManager` をオプション依存とすることで、ログ保存や履歴がない
    軽量モード（セッション無し）でも基底設定の閲覧・編集ができる。
    """

    def __init__(
        self,
        base_dir: Path | str,
        session_manager: object | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.configs_dir = self.base_dir / "configs"
        self.session_manager = session_manager

        self._base_config: SessionConfig = SessionConfig()
        self._active_config: SessionConfig | None = None

        self.load_base_config()

    # ──────────────────────────────────────────
    # 基底設定 I/O
    # ──────────────────────────────────────────

    def load_base_config(self) -> SessionConfig:
        """`configs/session_config.json` から基底設定を読み込む。

        ファイルが空 or 存在しない場合はデフォルト値の SessionConfig を保持する。
        """
        path = self.configs_dir / SESSION_CONFIG_FILENAME
        if not path.exists() or path.stat().st_size == 0:
            self._base_config = SessionConfig()
            return self._base_config
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._base_config = SessionConfig.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("session_config.json の読み込みに失敗: %s", e)
            self._base_config = SessionConfig()
        return self._base_config

    def save_base_config(self) -> Path:
        """基底設定を `configs/session_config.json` に書き出す。"""
        self.configs_dir.mkdir(parents=True, exist_ok=True)
        path = self.configs_dir / SESSION_CONFIG_FILENAME
        path.write_text(
            self._base_config.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return path

    @property
    def base_config(self) -> SessionConfig:
        return self._base_config

    def update_base_config(self, config: SessionConfig) -> None:
        self._base_config = config

    # ──────────────────────────────────────────
    # セッションのライフサイクル
    # ──────────────────────────────────────────

    def prepare_session(self, config: SessionConfig | dict) -> SessionConfig:
        """セッション開始前に設定を準備する（dict も受け付ける）。

        この時点ではまだ SessionManager は起動しない。
        ユーザーが UI で内容を確認・編集できるよう、active_config に保持するだけ。
        """
        if isinstance(config, dict):
            config = SessionConfig.model_validate(config)
        self._active_config = config
        logger.info("セッション準備完了: %s", config.session_name or "(無名)")
        return self._active_config

    def start_session(self, session_name: str | None = None) -> str | None:
        """準備済みのセッションを開始する。

        - active_config が無い場合は `SessionConfig(session_name=...)` を内部生成する
        - SessionManager がある場合はログフォルダを作成し、配下に session_config.json を保存
        - SessionManager が無い場合（セッション無しモード）は active_config だけ保持
        """
        if self._active_config is None:
            self._active_config = SessionConfig(session_name=session_name or "UnnamedSession")
        elif session_name:
            self._active_config = self._active_config.model_copy(
                update={"session_name": session_name}
            )

        name = self._active_config.session_name or "UnnamedSession"

        if self.session_manager is None:
            logger.info("セッションマネージャー未設定。軽量モードで開始: %s", name)
            return None

        self.session_manager.start_new_session(name)
        session_dir = getattr(self.session_manager, "current_session_dir", None)
        if session_dir is not None:
            self._active_config = self._active_config.model_copy(
                update={"history_ref": Path(session_dir).name}
            )
            out = Path(session_dir) / SESSION_CONFIG_FILENAME
            out.write_text(
                self._active_config.model_dump_json(indent=2),
                encoding="utf-8",
            )
            return Path(session_dir).name
        return None

    def end_session(self) -> None:
        """現セッションを閉じる。基底設定は保持されたまま。"""
        if self._active_config and self.session_manager is not None:
            session_dir = getattr(self.session_manager, "current_session_dir", None)
            if session_dir is not None:
                out = Path(session_dir) / SESSION_CONFIG_FILENAME
                try:
                    out.write_text(
                        self._active_config.model_dump_json(indent=2),
                        encoding="utf-8",
                    )
                except OSError as e:
                    logger.warning("セッション終了時の書き出しに失敗: %s", e)
        self._active_config = None

    @property
    def is_session_active(self) -> bool:
        return self._active_config is not None

    @property
    def active_config(self) -> SessionConfig | None:
        return self._active_config

    # ──────────────────────────────────────────
    # 統合ビュー（基底 + アクティブ）
    # ──────────────────────────────────────────

    def get_effective_config(self) -> SessionConfig:
        """基底とアクティブをマージした実効設定を返す。

        セッション無しの場合は基底のコピーを返すだけ。
        セッション有りの場合：
          - house_rules / mini_games は merge_rules / merge_mini_games で常に
            アクティブが優先される（priority に +1000 の下駄）
          - 文字列フィールドはアクティブが空でなければ上書き
          - settings / progress.flags は dict マージ（アクティブが上書き）
        """
        if self._active_config is None:
            return self._base_config.model_copy(deep=True)

        eff = self._base_config.model_copy(deep=True)
        a = self._active_config

        if a.session_name:
            eff.session_name = a.session_name
        if a.scenario_overview:
            eff.scenario_overview = a.scenario_overview
        if a.scenario_synopsis:
            eff.scenario_synopsis = a.scenario_synopsis
        if a.scenario_progress_notes:
            eff.scenario_progress_notes = a.scenario_progress_notes
        if a.pc_skills:
            eff.pc_skills = list(a.pc_skills)
        if a.pc_status_notes:
            eff.pc_status_notes = a.pc_status_notes
        if a.gm_instructions:
            eff.gm_instructions = a.gm_instructions
        if a.settings:
            eff.settings = {**eff.settings, **a.settings}
        # 現在のステータスは常にアクティブを採用（セッションのライブ状態）
        eff.status = a.status.model_copy()
        # シナリオ進行も同様
        merged_flags = {**eff.progress.flags, **a.progress.flags}
        eff.progress = a.progress.model_copy(update={"flags": merged_flags})

        eff.house_rules = merge_rules(eff.house_rules, a.house_rules)
        eff.mini_games = merge_mini_games(eff.mini_games, a.mini_games)
        eff.history_ref = a.history_ref or eff.history_ref
        return eff

    # ──────────────────────────────────────────
    # ハウスルール / ミニゲーム追加 API
    # ──────────────────────────────────────────

    def add_house_rule(self, rule: HouseRule | dict, *, scope: str = "session") -> HouseRule:
        """ハウスルールを追加する。scope='session' なら卓固有、'base' なら基底。"""
        if isinstance(rule, dict):
            rule = HouseRule.model_validate(rule)
        target = self._target_config(scope)
        target.house_rules = [r for r in target.house_rules if r.name != rule.name]
        target.house_rules.append(rule)
        return rule

    def add_mini_game(self, game: MiniGame | dict, *, scope: str = "session") -> MiniGame:
        if isinstance(game, dict):
            game = MiniGame.model_validate(game)
        target = self._target_config(scope)
        target.mini_games = [g for g in target.mini_games if g.name != game.name]
        target.mini_games.append(game)
        return game

    def list_active_house_rules(self) -> list[HouseRule]:
        """有効なハウスルール一覧（マージ済み・priority 降順）。"""
        rules = [r for r in self.get_effective_config().house_rules if r.enabled]
        return sorted(rules, key=lambda r: r.priority, reverse=True)

    def list_active_mini_games(self) -> list[MiniGame]:
        games = [g for g in self.get_effective_config().mini_games if g.enabled]
        return sorted(games, key=lambda g: g.priority, reverse=True)

    # ──────────────────────────────────────────
    # 内部ヘルパ
    # ──────────────────────────────────────────

    def _target_config(self, scope: str) -> SessionConfig:
        if scope == "session":
            if self._active_config is None:
                # セッション未開始でも追加できるよう、空の active を作る
                self._active_config = SessionConfig()
            return self._active_config
        if scope == "base":
            return self._base_config
        raise ValueError(f"未知の scope: {scope!r} (期待: 'session' または 'base')")
