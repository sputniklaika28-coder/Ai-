"""addon_base.py — アドオンの抽象基底クラス群。

BaseVTTAdapter パターンを踏襲し、すべてのアドオンが実装すべき
インターフェースを定義する。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .addon_models import AddonManifest


@dataclass
class AddonContext:
    """アドオンの on_load 時に渡される共有サービス群。"""

    adapter: Any  # BaseVTTAdapter | None
    lm_client: Any  # LMClient
    knowledge_manager: Any  # KnowledgeManager | None
    session_manager: Any  # SessionManager
    character_manager: Any  # CharacterManager
    root_dir: Path


@dataclass
class ToolExecutionContext:
    """ツール実行時に渡されるコンテキスト。"""

    char_name: str
    tool_call_id: str
    adapter: Any  # BaseVTTAdapter | None
    connector: Any  # CCFoliaConnector


class AddonBase(ABC):
    """全アドオンの抽象基底クラス。"""

    manifest: AddonManifest
    addon_dir: Path  # アドオンフォルダの絶対パス

    @abstractmethod
    def on_load(self, context: AddonContext) -> None:
        """アドオンがロードされた時に呼ばれる。共有サービスを受け取る。"""

    def on_unload(self) -> None:
        """アドオンがアンロードされた時に呼ばれる。リソース解放用。"""

    def get_tools(self) -> list[dict]:
        """OpenAI function-calling 形式のツール定義リストを返す。"""
        return []

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """ツールコールを処理する。

        Returns:
            (finished, result_json) のタプル。
            finished=True はエージェントループの終了を意味する。
        """
        return False, json.dumps({"error": f"未対応ツール: {tool_name}"}, ensure_ascii=False)


class RuleSystemAddon(AddonBase):
    """ルールシステムアドオンの拡張基底クラス。

    TRPGのゲームシステム固有のプロンプト・フェイズ・判定ロジックを提供する。
    """

    def get_system_prompt_override(self) -> str | None:
        """システムプロンプトに注入するテキストを返す。None で上書きなし。"""
        return None

    def get_world_setting(self) -> str:
        """世界観設定テキストを返す。"""
        return ""

    def get_phase_keywords(self) -> dict[str, list[str]]:
        """フェイズ検出キーワードを返す。{phase_name: [keywords...]}"""
        return {}

    def get_prompt_templates(self) -> dict | None:
        """プロンプトテンプレート辞書を返す。None でデフォルトを使用。"""
        return None

    def interpret_character_sheet(self, sheet_data: dict) -> str:
        """キャラクターシートを自然言語に解釈する。"""
        return ""


class ToolAddon(AddonBase):
    """ツールアドオンの拡張基底クラス。

    AIが使うツールや、ユーザー向けGUIタブを提供する。
    """

    def get_gui_tab_class(self) -> type | None:
        """ttk.Frame サブクラスを返す。GUIタブが不要なら None。"""
        return None
