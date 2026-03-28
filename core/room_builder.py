"""room_builder.py — CCFolia ルーム自動構築オーケストレーター。

構造化されたルーム定義（JSON/dict）から CCFolia ルームを自動構築する。
Phase 1（Browser Use）+ Phase 2（AssetUploader, VisionCanvasController）の
全機能を統合し、ルーム作成→アセットアップロード→背景設定→BGM追加→
キャラクター配置の全パイプラインを一括実行する。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────


@dataclass
class CharacterPlacement:
    """ルームに配置するキャラクターの定義。"""

    name: str
    image_path: str = ""
    position: str = ""  # 自然言語（"十字路の近く" 等）→ VLM で座標特定
    grid_x: int | None = None
    grid_y: int | None = None
    ccfolia_data: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CharacterPlacement:
        return cls(
            name=data.get("name", ""),
            image_path=data.get("image_path", ""),
            position=data.get("position", ""),
            grid_x=data.get("grid_x"),
            grid_y=data.get("grid_y"),
            ccfolia_data=data.get("ccfolia_data", {}),
        )


@dataclass
class RoomDefinition:
    """ルーム全体の定義。"""

    name: str
    description: str = ""
    background_image: str = ""
    bgm: list[dict] = field(default_factory=list)  # [{"file_path": str, "name": str}]
    characters: list[CharacterPlacement] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> RoomDefinition:
        chars = [
            CharacterPlacement.from_dict(c) for c in data.get("characters", [])
        ]
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            background_image=data.get("background_image", ""),
            bgm=data.get("bgm", []),
            characters=chars,
        )

    def validate(self) -> list[str]:
        """定義の妥当性を検証する。エラーメッセージのリストを返す。"""
        errors: list[str] = []
        if not self.name:
            errors.append("ルーム名 (name) が未指定です")
        if self.background_image and not Path(self.background_image).exists():
            errors.append(f"背景画像が見つかりません: {self.background_image}")
        for bgm in self.bgm:
            fp = bgm.get("file_path", "")
            if fp and not Path(fp).exists():
                errors.append(f"BGMファイルが見つかりません: {fp}")
        for ch in self.characters:
            if not ch.name:
                errors.append("キャラクター名が未指定の要素があります")
            if ch.image_path and not Path(ch.image_path).exists():
                errors.append(f"トークン画像が見つかりません: {ch.image_path}")
        return errors


@dataclass
class StepResult:
    """パイプラインの各ステップの実行結果。"""

    step: str
    success: bool
    detail: str = ""
    error: str = ""


# ──────────────────────────────────────────
# RoomBuilder
# ──────────────────────────────────────────


class RoomBuilder:
    """CCFolia ルーム自動構築オーケストレーター。

    BrowserUseVTTAdapter の全機能を使い、構造化定義から
    ルームを一括構築する。各ステップは best-effort で、
    ルーム作成以外の失敗はスキップして続行する。
    """

    def __init__(
        self,
        adapter: object,
        on_progress: Callable[[StepResult], None] | None = None,
    ) -> None:
        self._adapter = adapter
        self._on_progress = on_progress
        self._results: list[StepResult] = []

    @property
    def results(self) -> list[StepResult]:
        """直近の build_room() の全ステップ結果。"""
        return list(self._results)

    # ──────────────────────────────────────────
    # フルパイプライン
    # ──────────────────────────────────────────

    def build_room(self, definition: RoomDefinition) -> list[StepResult]:
        """ルーム定義から全自動でルームを構築する。

        Args:
            definition: ルーム定義。

        Returns:
            各ステップの実行結果リスト。
        """
        self._results = []

        errors = definition.validate()
        if errors:
            r = StepResult(
                step="validate", success=False, error="; ".join(errors),
            )
            self._report(r)
            return self._results

        # Step 1: ルーム作成（致命的 — 失敗時は中断）
        r = self.create_room(definition.name)
        if not r.success:
            return self._results

        # Step 2: 背景画像
        if definition.background_image:
            self.set_background(definition.background_image)

        # Step 3: BGM
        for bgm in definition.bgm:
            self.add_bgm(bgm.get("file_path", ""), bgm.get("name", ""))

        # Step 4: キャラクター配置
        for char in definition.characters:
            self.place_character(char)

        return self._results

    # ──────────────────────────────────────────
    # インクリメンタル API
    # ──────────────────────────────────────────

    def create_room(self, name: str) -> StepResult:
        """ルームを作成する。"""
        try:
            result = self._adapter.create_room(name)  # type: ignore[union-attr]
            success = getattr(result, "success", bool(result))
            error = getattr(result, "error", "") if not success else ""
            r = StepResult(
                step="create_room", success=success,
                detail=f"ルーム「{name}」", error=error,
            )
        except Exception as e:
            r = StepResult(step="create_room", success=False, error=str(e))
        self._report(r)
        return r

    def set_background(self, image_path: str) -> StepResult:
        """背景画像をアップロードして設定する。"""
        try:
            url = self._adapter.upload_asset(image_path, "background")  # type: ignore[union-attr]
            if not url:
                r = StepResult(
                    step="set_background", success=False,
                    error="画像アップロード失敗",
                )
                self._report(r)
                return r
            result = self._adapter.set_background(url)  # type: ignore[union-attr]
            success = getattr(result, "success", bool(result))
            r = StepResult(
                step="set_background", success=success,
                detail=url[:60] if url else "",
            )
        except Exception as e:
            r = StepResult(step="set_background", success=False, error=str(e))
        self._report(r)
        return r

    def add_bgm(self, file_path: str, name: str = "") -> StepResult:
        """BGM をアップロードして追加する。"""
        try:
            url = self._adapter.upload_asset(file_path, "bgm")  # type: ignore[union-attr]
            if not url:
                r = StepResult(
                    step="add_bgm", success=False,
                    error="BGMアップロード失敗",
                )
                self._report(r)
                return r
            bgm_name = name or Path(file_path).stem
            result = self._adapter.switch_bgm(bgm_name)  # type: ignore[union-attr]
            success = getattr(result, "success", bool(result))
            r = StepResult(
                step="add_bgm", success=success, detail=bgm_name,
            )
        except Exception as e:
            r = StepResult(step="add_bgm", success=False, error=str(e))
        self._report(r)
        return r

    def place_character(self, char: CharacterPlacement) -> StepResult:
        """キャラクターを配置する。

        1. トークン画像があればアップロード
        2. ccfolia_data で駒を生成
        3. grid 座標指定があれば move_piece、自然言語位置なら VLM で配置
        """
        step_name = f"place_character:{char.name}"
        try:
            # トークン画像アップロード
            if char.image_path:
                self._adapter.upload_asset(char.image_path, "token")  # type: ignore[union-attr]

            # 駒を生成
            ccfolia_data = char.ccfolia_data or {"name": char.name}
            if not self._adapter.spawn_piece(ccfolia_data):  # type: ignore[union-attr]
                r = StepResult(
                    step=step_name, success=False, error="駒の生成に失敗",
                )
                self._report(r)
                return r

            # 位置指定がある場合は移動
            if char.grid_x is not None and char.grid_y is not None:
                self._adapter.move_piece(  # type: ignore[union-attr]
                    char.name, char.grid_x, char.grid_y,
                )
                r = StepResult(
                    step=step_name, success=True,
                    detail=f"grid ({char.grid_x}, {char.grid_y})",
                )
            elif char.position:
                # VLM で位置特定して配置
                try:
                    vision = self._adapter.get_vision_controller()  # type: ignore[union-attr]
                    target = vision.find_empty_space(near=char.position)
                    if target:
                        vision.drag_piece(
                            (0, 0), target,
                        )
                        r = StepResult(
                            step=step_name, success=True,
                            detail=f"VLM: {char.position} → ({target[0]}, {target[1]})",
                        )
                    else:
                        r = StepResult(
                            step=step_name, success=True,
                            detail=f"VLM位置特定失敗、デフォルト位置に配置: {char.position}",
                        )
                except Exception as e:
                    r = StepResult(
                        step=step_name, success=True,
                        detail=f"VLM利用不可、デフォルト位置に配置: {e}",
                    )
            else:
                r = StepResult(
                    step=step_name, success=True, detail="デフォルト位置",
                )

        except Exception as e:
            r = StepResult(step=step_name, success=False, error=str(e))
        self._report(r)
        return r

    # ──────────────────────────────────────────
    # 内部ヘルパー
    # ──────────────────────────────────────────

    def _report(self, result: StepResult) -> None:
        """ステップ結果を記録し、コールバックがあれば呼び出す。"""
        self._results.append(result)
        status = "✓" if result.success else "✗"
        logger.info("[%s] %s: %s %s", status, result.step, result.detail, result.error)
        if self._on_progress:
            try:
                self._on_progress(result)
            except Exception:
                pass
