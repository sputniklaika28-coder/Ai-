"""addon_manager.py — アドオンの探索・ロード・ツール集約を担うマネージャー。

addons/ フォルダを監視し、addon.json を Pydantic でパースして
各アドオンを安全にロード・管理する。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .addon_base import AddonBase, AddonContext, RuleSystemAddon, ToolAddon, ToolExecutionContext
from .addon_models import AddonManifest

logger = logging.getLogger(__name__)


class AddonManager:
    """アドオンの探索・ロード・ツール集約・ディスパッチを管理する。"""

    def __init__(self, addons_dir: Path, core_dir: Path | None = None) -> None:
        self.addons_dir = addons_dir
        self.core_dir = core_dir
        self._addons: dict[str, AddonBase] = {}  # id -> instance
        self._load_order: list[str] = []
        self._manifests: dict[str, AddonManifest] = {}
        self._tool_registry: dict[str, AddonBase] = {}  # tool_name -> addon

    # ──────────────────────────────────────────
    # 探索
    # ──────────────────────────────────────────

    def discover(self) -> list[AddonManifest]:
        """addons/ フォルダをスキャンして addon.json を検出・バリデーションする。"""
        manifests: list[AddonManifest] = []

        if not self.addons_dir.is_dir():
            logger.info("アドオンフォルダが見つかりません: %s", self.addons_dir)
            return manifests

        for addon_dir in sorted(self.addons_dir.iterdir()):
            if not addon_dir.is_dir():
                continue
            manifest_path = addon_dir / "addon.json"
            if not manifest_path.exists():
                continue
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    data = json.load(f)
                manifest = AddonManifest(**data)
                self._manifests[manifest.id] = manifest
                manifests.append(manifest)
                logger.info("アドオン検出: %s (%s) v%s", manifest.name, manifest.id, manifest.version)
            except Exception as e:
                logger.warning("アドオンマニフェスト読み込み失敗: %s: %s", manifest_path, e)

        return manifests

    # ──────────────────────────────────────────
    # ロード
    # ──────────────────────────────────────────

    def load_all(self, context: AddonContext) -> None:
        """検出済みの全アドオンを依存順にロードする。"""
        if not self._manifests:
            self.discover()

        load_order = self._resolve_dependencies()

        for addon_id in load_order:
            try:
                self.load_addon(addon_id, context)
            except Exception as e:
                logger.error("アドオンロード失敗: %s: %s", addon_id, e)

    def load_addon(self, addon_id: str, context: AddonContext) -> None:
        """単一のアドオンをロードする。"""
        if addon_id in self._addons:
            logger.warning("アドオン %s は既にロード済みです", addon_id)
            return

        manifest = self._manifests.get(addon_id)
        if manifest is None:
            raise ValueError(f"アドオン {addon_id} のマニフェストが見つかりません")

        # rule_system は1つだけ許可
        if manifest.type == "rule_system":
            existing = self.get_active_rule_system()
            if existing is not None:
                logger.warning(
                    "ルールシステム %s は既にアクティブです。%s をスキップします",
                    existing.manifest.id,
                    addon_id,
                )
                return

        addon_dir = self.addons_dir / addon_id
        entry_path = addon_dir / manifest.entry_point

        if not entry_path.exists():
            raise FileNotFoundError(f"エントリポイントが見つかりません: {entry_path}")

        # 動的インポート
        module_name = f"addons.{addon_id}.{manifest.entry_point.removesuffix('.py')}"
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"モジュールスペック作成失敗: {entry_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        addon_cls = getattr(module, manifest.class_name, None)
        if addon_cls is None:
            raise AttributeError(
                f"クラス {manifest.class_name} が {entry_path} に見つかりません"
            )

        instance: AddonBase = addon_cls()
        instance.manifest = manifest
        instance.addon_dir = addon_dir
        instance.on_load(context)

        # ツール登録
        for tool_def in instance.get_tools():
            tool_name = tool_def.get("function", {}).get("name", "")
            if not tool_name:
                continue
            if tool_name in self._tool_registry:
                logger.warning(
                    "ツール名 %s が重複しています (%s vs %s)。先にロードされた方を優先します",
                    tool_name,
                    self._tool_registry[tool_name].manifest.id,
                    addon_id,
                )
                continue
            self._tool_registry[tool_name] = instance

        self._addons[addon_id] = instance
        self._load_order.append(addon_id)
        logger.info("アドオンロード完了: %s", addon_id)

    def unload_addon(self, addon_id: str) -> None:
        """アドオンをアンロードする。"""
        addon = self._addons.pop(addon_id, None)
        if addon is None:
            return

        # ツール登録解除
        to_remove = [name for name, a in self._tool_registry.items() if a is addon]
        for name in to_remove:
            del self._tool_registry[name]

        addon.on_unload()

        if addon_id in self._load_order:
            self._load_order.remove(addon_id)

        logger.info("アドオンアンロード完了: %s", addon_id)

    # ──────────────────────────────────────────
    # ツール集約・ディスパッチ
    # ──────────────────────────────────────────

    def get_all_tools(self) -> list[dict]:
        """全ロード済みアドオンのツール定義を集約して返す。"""
        tools: list[dict] = []
        for addon_id in self._load_order:
            addon = self._addons[addon_id]
            tools.extend(addon.get_tools())
        return tools

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """ツール名に対応するアドオンにディスパッチする。"""
        addon = self._tool_registry.get(tool_name)
        if addon is None:
            return False, json.dumps(
                {"error": f"未知のアドオンツール: {tool_name}"}, ensure_ascii=False
            )
        return addon.execute_tool(tool_name, tool_args, context)

    # ──────────────────────────────────────────
    # クエリ
    # ──────────────────────────────────────────

    def get_active_rule_system(self) -> RuleSystemAddon | None:
        """アクティブなルールシステムアドオンを返す（1つのみ）。"""
        for addon in self._addons.values():
            if isinstance(addon, RuleSystemAddon):
                return addon
        return None

    def get_gui_tabs(self) -> list[tuple[str, type]]:
        """(ラベル, Frameクラス) のリストを返す。"""
        tabs: list[tuple[str, type]] = []
        for addon in self._addons.values():
            if isinstance(addon, ToolAddon):
                tab_cls = addon.get_gui_tab_class()
                if tab_cls is not None:
                    label = addon.manifest.gui_tab_label or addon.manifest.name
                    tabs.append((label, tab_cls))
        return tabs

    @property
    def loaded_addons(self) -> dict[str, AddonBase]:
        """ロード済みアドオンの辞書を返す。"""
        return dict(self._addons)

    @property
    def manifests(self) -> dict[str, AddonManifest]:
        """検出済みのマニフェスト一覧を返す"""
        return dict(self._manifests)

    def load_enabled(self, enabled_ids: list[str], context: AddonContext) -> None:
        """指定されたIDリストのアドオンのみをロードする"""
        if not self._manifests:
            self.discover()

        for addon_id in enabled_ids:
            if addon_id in self._manifests:
                try:
                    self.load_addon(addon_id, context)
                except Exception as e:
                    logger.error("アドオンロード失敗: %s: %s", addon_id, e)
            else:
                logger.warning("有効化されたアドオンが見つかりません: %s", addon_id)

    # ──────────────────────────────────────────
    # 依存解決
    # ──────────────────────────────────────────

    def _resolve_dependencies(self) -> list[str]:
        """トポロジカルソートで依存順のロード順序を返す。"""
        visited: set[str] = set()
        order: list[str] = []
        in_progress: set[str] = set()

        def visit(addon_id: str) -> None:
            if addon_id in visited:
                return
            if addon_id in in_progress:
                logger.warning("循環依存を検出: %s", addon_id)
                return
            if addon_id not in self._manifests:
                logger.warning("依存アドオンが見つかりません: %s", addon_id)
                return

            in_progress.add(addon_id)
            for dep in self._manifests[addon_id].dependencies:
                visit(dep)
            in_progress.discard(addon_id)
            visited.add(addon_id)
            order.append(addon_id)

        for addon_id in self._manifests:
            visit(addon_id)

        return order
