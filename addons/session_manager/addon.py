"""session_manager addon — 全ルールシステム共通のセッション記録係。

core/session_manager.py の SessionManager を共通サービスとして提供し、
チャット履歴の保存やゲームの進行状況を JSONL 形式で記録します。

提供するツール:
  - start_session       : 新規セッションを開始する
  - log_message         : メッセージをセッションログに記録する
  - list_sessions       : 過去のセッション一覧を返す
  - get_session_history : 指定セッションの履歴を返す
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext

logger = logging.getLogger(__name__)


class SessionManagerAddon(ToolAddon):
    """セッション記録の永続化を担う共通アドオン。

    セッションデータは sessions/<timestamp>_<name>/ に保存され、
    core/session_manager.py と互換性を保つ。
    """

    def on_load(self, context: AddonContext) -> None:
        self._root_dir = context.root_dir
        self._sessions_dir = self._root_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # core の SessionManager を保持（実際のログ記録に使用）
        self._core_session_manager = context.session_manager
        logger.info(
            "SessionManagerAddon ロード完了。セッション保存先: %s", self._sessions_dir
        )

    # ── ツール定義 ────────────────────────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "start_session",
                    "description": "新しいセッションを開始してログファイルを作成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_name": {
                                "type": "string",
                                "description": "セッション名（例: 第3話_廃校の怪異）",
                            },
                        },
                        "required": ["session_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "log_message",
                    "description": "現在のセッションにメッセージを記録する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "speaker": {
                                "type": "string",
                                "description": "発言者名（GM、キャラクター名など）",
                            },
                            "body": {
                                "type": "string",
                                "description": "発言・行動の内容",
                            },
                        },
                        "required": ["speaker", "body"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_sessions",
                    "description": "保存済みセッションの一覧を返す",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "返す件数の上限（デフォルト: 20）",
                            },
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_session_history",
                    "description": "指定したセッションのチャット履歴を返す",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_folder": {
                                "type": "string",
                                "description": "セッションフォルダ名（list_sessions で取得できる）",
                            },
                            "last_n": {
                                "type": "integer",
                                "description": "末尾から何件取得するか（デフォルト: 全件）",
                            },
                        },
                        "required": ["session_folder"],
                    },
                },
            },
        ]

    # ── ツール実行 ────────────────────────────────────────────────────────────

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        try:
            if tool_name == "start_session":
                return False, self._start_session(tool_args["session_name"])
            elif tool_name == "log_message":
                return False, self._log_message(tool_args["speaker"], tool_args["body"])
            elif tool_name == "list_sessions":
                return False, self._list_sessions(tool_args.get("limit", 20))
            elif tool_name == "get_session_history":
                return False, self._get_session_history(
                    tool_args["session_folder"],
                    tool_args.get("last_n"),
                )
            else:
                return False, json.dumps(
                    {"error": f"未知のツール: {tool_name}"}, ensure_ascii=False
                )
        except KeyError as e:
            return False, json.dumps(
                {"error": f"必須パラメータが不足しています: {e}"}, ensure_ascii=False
            )
        except Exception as e:
            logger.error("ツール実行エラー %s: %s", tool_name, e)
            return False, json.dumps({"error": str(e)}, ensure_ascii=False)

    # ── 内部ロジック ──────────────────────────────────────────────────────────

    def _start_session(self, session_name: str) -> str:
        """core/session_manager.py の start_new_session を呼び出す。"""
        try:
            if self._core_session_manager is not None:
                self._core_session_manager.start_new_session(session_name)
                folder = (
                    self._core_session_manager.current_session_dir.name
                    if self._core_session_manager.current_session_dir
                    else "unknown"
                )
                return json.dumps(
                    {"success": True, "session_folder": folder, "session_name": session_name},
                    ensure_ascii=False,
                )
        except Exception as e:
            logger.error("セッション開始エラー: %s", e)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

        return json.dumps(
            {"success": False, "error": "SessionManager が利用できません"}, ensure_ascii=False
        )

    def _log_message(self, speaker: str, body: str) -> str:
        """現在のセッションにメッセージを記録する。"""
        try:
            if self._core_session_manager is not None:
                self._core_session_manager.log_message(speaker, body)
                return json.dumps({"success": True}, ensure_ascii=False)
        except Exception as e:
            logger.error("ログ記録エラー: %s", e)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

        return json.dumps(
            {"success": False, "error": "SessionManager が利用できません"}, ensure_ascii=False
        )

    def _list_sessions(self, limit: int = 20) -> str:
        """保存済みセッション一覧を返す（最新順）。"""
        folders = sorted(
            [d.name for d in self._sessions_dir.iterdir() if d.is_dir()],
            reverse=True,
        )[:limit]
        return json.dumps(
            {"sessions": folders, "total": len(folders)}, ensure_ascii=False
        )

    def _get_session_history(self, session_folder: str, last_n: int | None = None) -> str:
        """指定セッションのチャット履歴を返す。"""
        log_path = self._sessions_dir / session_folder / "chat_log.jsonl"
        if not log_path.exists():
            return json.dumps(
                {"success": False, "error": f"{session_folder} のログが見つかりません"},
                ensure_ascii=False,
            )
        try:
            history: list[dict] = []
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        history.append(json.loads(line))

            if last_n is not None and last_n > 0:
                history = history[-last_n:]

            return json.dumps(
                {"success": True, "session": session_folder, "messages": history},
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    # ── 直接アクセス用 API ────────────────────────────────────────────────────

    @property
    def current_session_dir(self) -> Path | None:
        """現在のセッションディレクトリを返す（ツール非経由）。"""
        if self._core_session_manager:
            return self._core_session_manager.current_session_dir
        return None

    def get_recent_history(self, n: int = 20) -> list[dict]:
        """最新 n 件のメッセージ履歴を返す（ツール非経由）。"""
        if self._core_session_manager:
            return self._core_session_manager.history[-n:]
        return []
