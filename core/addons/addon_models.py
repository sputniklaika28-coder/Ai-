"""addon_models.py — アドオンマニフェストの Pydantic スキーマ定義。

各アドオンフォルダ内の addon.json を安全にパース・バリデーションする。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AddonManifest(BaseModel):
    """アドオンマニフェスト (addon.json) のスキーマ。"""

    id: str = Field(..., description="アドオンの一意識別子 (例: tactical_exorcist)")
    name: str = Field(..., description="表示名")
    version: str = Field(default="1.0.0", description="セマンティックバージョン (例: 1.0.0)")
    type: Literal["rule_system", "tool"] = Field(..., description="アドオン種別")
    description: str = ""
    author: str = ""
    dependencies: list[str] = Field(default_factory=list, description="依存アドオンID")
    entry_point: str = Field(default="addon.py", description="エントリポイントファイル名")
    class_name: str = Field(..., description="インスタンス化するクラス名")

    # rule_system 専用フィールド
    prompts_override: str | None = Field(
        default=None, description="プロンプト定義ファイル (相対パス)"
    )
    world_setting: str | None = Field(
        default=None,
        description="世界観設定テキストファイル名。configs/ からの相対パスとして解決する。",
    )
    world_setting_json: str | None = Field(
        default=None,
        description="世界観設定 JSON ファイル名。configs/ からの相対パスとして解決する。",
    )
    characters: str | None = Field(
        default=None, description="キャラクター定義ファイル (相対パス)"
    )

    # tool 専用フィールド
    tools: list[str] = Field(default_factory=list, description="提供するツール名のリスト")
    gui_tab: str | None = Field(
        default=None, description="GUIタブクラス名 (ttk.Frame サブクラス)"
    )
    gui_tab_label: str | None = Field(default=None, description="GUIタブの表示ラベル")
