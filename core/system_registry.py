"""system_registry.py — TRPGルールシステムのレジストリとアクティブ管理。

`configs/systems.json` を単一の真実とし、どのシステムを "active" にするかを
永続化する。各システムは固有の世界観/キャラクター/プロンプト JSON ファイルを
持てる（デフォルトは既存のトップレベル JSON を共有し後方互換）。

RuleSystemAddon の discovery と連携し、将来のシステム追加を想定する。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_ID = "tactical_exorcist"
DEFAULT_SYSTEM_LABEL = "タクティカル祓魔師"


@dataclass
class SystemEntry:
    """単一TRPGシステムの設定。"""

    id: str
    label: str
    world_setting_file: Path
    characters_file: Path
    prompts_file: Path
    addon_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "world_setting_file": self.world_setting_file.name,
            "characters_file": self.characters_file.name,
            "prompts_file": self.prompts_file.name,
            "addon_id": self.addon_id,
        }


class SystemRegistry:
    """TRPGシステムの一覧とアクティブ管理。"""

    def __init__(self, configs_dir: Path) -> None:
        self._configs_dir = configs_dir
        self._registry_file = configs_dir / "systems.json"
        self._systems: dict[str, SystemEntry] = {}
        self._active_id: str = DEFAULT_SYSTEM_ID
        self._listeners: list[callable] = []
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        if self._registry_file.exists():
            try:
                data = json.loads(self._registry_file.read_text(encoding="utf-8"))
                self._active_id = data.get("active_system", DEFAULT_SYSTEM_ID)
                for sys_id, entry_data in data.get("systems", {}).items():
                    self._systems[sys_id] = SystemEntry(
                        id=sys_id,
                        label=entry_data.get("label", sys_id),
                        world_setting_file=self._resolve_file(
                            entry_data.get("world_setting_file", "world_setting.json")
                        ),
                        characters_file=self._resolve_file(
                            entry_data.get("characters_file", "characters.json")
                        ),
                        prompts_file=self._resolve_file(
                            entry_data.get("prompts_file", "prompts.json")
                        ),
                        addon_id=entry_data.get("addon_id"),
                    )
                return
            except Exception as e:
                logger.warning("systems.json の読み込みに失敗、デフォルトで再構築: %s", e)

        # シード: 既存の tactical_exorcist を唯一の登録として作成（後方互換）
        self._systems[DEFAULT_SYSTEM_ID] = SystemEntry(
            id=DEFAULT_SYSTEM_ID,
            label=DEFAULT_SYSTEM_LABEL,
            world_setting_file=self._configs_dir / "world_setting.json",
            characters_file=self._configs_dir / "characters.json",
            prompts_file=self._configs_dir / "prompts.json",
            addon_id="tactical_exorcist",
        )
        self._active_id = DEFAULT_SYSTEM_ID
        self.save()

    def _resolve_file(self, filename: str) -> Path:
        return self._configs_dir / filename

    def save(self) -> None:
        data = {
            "active_system": self._active_id,
            "systems": {sid: entry.to_dict() for sid, entry in self._systems.items()},
        }
        self._registry_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ──────────────────────────────────────────

    def list_systems(self) -> list[SystemEntry]:
        return list(self._systems.values())

    def get_active(self) -> SystemEntry:
        entry = self._systems.get(self._active_id)
        if entry is None:
            entry = next(iter(self._systems.values()))
            self._active_id = entry.id
        return entry

    def set_active(self, system_id: str) -> SystemEntry:
        if system_id not in self._systems:
            raise KeyError(f"未登録のシステムID: {system_id}")
        self._active_id = system_id
        self.save()
        entry = self._systems[system_id]
        for cb in list(self._listeners):
            try:
                cb(entry)
            except Exception as e:
                logger.warning("system_changed listener error: %s", e)
        return entry

    def register(self, entry: SystemEntry) -> None:
        """新しいシステムを登録（既存は上書き）。"""
        self._systems[entry.id] = entry
        self.save()

    def discover_from_addon_manager(self, addon_manager: Any) -> None:
        """AddonManager から RuleSystemAddon を発見し、未登録のものを追加。

        既存エントリは上書きしない（ユーザーが編集した label 等を保持）。
        """
        try:
            manifests = addon_manager.manifests
        except Exception:
            return

        for addon_id, manifest in manifests.items():
            if getattr(manifest, "type", None) != "rule_system":
                continue
            if addon_id in self._systems:
                continue
            self._systems[addon_id] = SystemEntry(
                id=addon_id,
                label=getattr(manifest, "name", addon_id),
                world_setting_file=self._configs_dir / f"world_setting_{addon_id}.json",
                characters_file=self._configs_dir / f"characters_{addon_id}.json",
                prompts_file=self._configs_dir / f"prompts_{addon_id}.json",
                addon_id=addon_id,
            )
        self.save()

    def add_listener(self, callback) -> None:
        """set_active 時に呼ばれるコールバックを登録。引数は SystemEntry。"""
        self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)
