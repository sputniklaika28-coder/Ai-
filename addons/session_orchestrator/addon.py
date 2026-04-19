"""session_orchestrator addon — 統合セッション管理。

シナリオ概要 / あらすじ / PCスキル / セッション設定 / セッション履歴 /
現在のステータス / シナリオ進行 を統合し、セッションを事前準備して
進行できるようにする共通アドオン。

セッションごとのハウスルール・追加のミニゲームをサポートし、
それらは常にその他の設定より優先される。
セッション無しでも基底設定の閲覧・編集ができる。
"""

from __future__ import annotations

import json
import logging

from core.addons.addon_base import AddonContext, ToolAddon, ToolExecutionContext
from core.session_orchestrator import SessionOrchestrator

logger = logging.getLogger(__name__)


class SessionOrchestratorAddon(ToolAddon):
    """SessionOrchestrator を AI ツール / 他アドオンへ公開するアドオン。"""

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        self._orchestrator = SessionOrchestrator(
            base_dir=context.root_dir,
            session_manager=context.session_manager,
        )
        logger.info(
            "SessionOrchestratorAddon ロード完了 (セッション無しでも動作可)"
        )

    @property
    def orchestrator(self) -> SessionOrchestrator:
        """他のコード（GMDirector など）から直接参照する用。"""
        return self._orchestrator

    # ──────────────────────────────────────────
    # ツール定義
    # ──────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "prepare_session",
                    "description": "シナリオ概要・PCスキル・ハウスルール等を含むセッションを事前準備する（まだ開始しない）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "config": {
                                "type": "object",
                                "description": "SessionConfig 互換 dict（session_name, scenario_overview, scenario_synopsis, pc_skills, settings, status, progress, house_rules, mini_games）",
                            }
                        },
                        "required": ["config"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "start_orchestrated_session",
                    "description": "事前準備済みのセッションを開始しログ保存を開始する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_name": {
                                "type": "string",
                                "description": "セッション名の上書き（省略時は prepare_session の値を使用）",
                            }
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "end_orchestrated_session",
                    "description": "現セッションを終了し、最終的な設定を保存する",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_effective_session_config",
                    "description": "基底とアクティブをマージした実効セッション設定を返す（セッション無しなら基底のみ）",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_house_rule",
                    "description": "ハウスルールを追加する。セッション固有なら scope='session'、基底なら scope='base'。同名ルールは上書き。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "rule": {
                                "type": "object",
                                "description": "HouseRule 互換 dict（name, description, enabled, priority, params）",
                            },
                            "scope": {
                                "type": "string",
                                "enum": ["session", "base"],
                                "description": "適用範囲（デフォルト: session）",
                            },
                        },
                        "required": ["rule"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_mini_game",
                    "description": "セッションで使える追加のミニゲームを登録する。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "game": {
                                "type": "object",
                                "description": "MiniGame 互換 dict（name, description, trigger, enabled, priority, params）",
                            },
                            "scope": {
                                "type": "string",
                                "enum": ["session", "base"],
                            },
                        },
                        "required": ["game"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_active_house_rules",
                    "description": "現在有効なハウスルール一覧（セッション > 基底のマージ済み）を返す",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_active_mini_games",
                    "description": "現在有効な追加のミニゲーム一覧を返す",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_base_session_config",
                    "description": "現在の基底設定を configs/session_config.json に書き出す",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    # ──────────────────────────────────────────
    # ツール実行
    # ──────────────────────────────────────────

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        try:
            if tool_name == "prepare_session":
                cfg = self._orchestrator.prepare_session(tool_args["config"])
                return False, json.dumps(
                    {"success": True, "session_name": cfg.session_name},
                    ensure_ascii=False,
                )

            if tool_name == "start_orchestrated_session":
                folder = self._orchestrator.start_session(tool_args.get("session_name"))
                return False, json.dumps(
                    {
                        "success": True,
                        "session_folder": folder,
                        "lightweight": folder is None,
                    },
                    ensure_ascii=False,
                )

            if tool_name == "end_orchestrated_session":
                self._orchestrator.end_session()
                return False, json.dumps({"success": True}, ensure_ascii=False)

            if tool_name == "get_effective_session_config":
                eff = self._orchestrator.get_effective_config()
                return False, eff.model_dump_json()

            if tool_name == "add_house_rule":
                rule = self._orchestrator.add_house_rule(
                    tool_args["rule"], scope=tool_args.get("scope", "session")
                )
                return False, json.dumps(
                    {"success": True, "rule": rule.model_dump()},
                    ensure_ascii=False,
                )

            if tool_name == "add_mini_game":
                game = self._orchestrator.add_mini_game(
                    tool_args["game"], scope=tool_args.get("scope", "session")
                )
                return False, json.dumps(
                    {"success": True, "game": game.model_dump()},
                    ensure_ascii=False,
                )

            if tool_name == "list_active_house_rules":
                rules = [r.model_dump() for r in self._orchestrator.list_active_house_rules()]
                return False, json.dumps({"rules": rules}, ensure_ascii=False)

            if tool_name == "list_active_mini_games":
                games = [g.model_dump() for g in self._orchestrator.list_active_mini_games()]
                return False, json.dumps({"games": games}, ensure_ascii=False)

            if tool_name == "save_base_session_config":
                path = self._orchestrator.save_base_config()
                return False, json.dumps(
                    {"success": True, "path": str(path)}, ensure_ascii=False
                )

            return False, json.dumps(
                {"error": f"未知のツール: {tool_name}"}, ensure_ascii=False
            )

        except KeyError as e:
            return False, json.dumps(
                {"error": f"必須パラメータが不足: {e}"}, ensure_ascii=False
            )
        except Exception as e:
            logger.error("ツール実行エラー %s: %s", tool_name, e)
            return False, json.dumps({"error": str(e)}, ensure_ascii=False)
