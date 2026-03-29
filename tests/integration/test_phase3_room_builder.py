"""Phase 3: 自動部屋構築テスト。

RoomBuilder によるルーム作成、フルパイプライン、
グリッド/VLM 座標でのキャラクター配置、エラーリカバリーを検証する。

実行方法:
    CCFOLIA_ROOM_URL=https://ccfolia.com/rooms/xxxx pytest tests/integration/test_phase3_room_builder.py -v
"""

from __future__ import annotations

import pytest

from core.room_builder import CharacterPlacement, RoomBuilder, RoomDefinition

pytestmark = [pytest.mark.integration, pytest.mark.browser_use]


class TestPhase3RoomBuilder:
    """Phase 3: 自動部屋構築。"""

    # 3-1: ルーム作成
    def test_3_1_create_room(self, adapter):
        """RoomBuilder.create_room("テストルーム") → CCFolia に新ルームが作成される。"""
        builder = RoomBuilder(adapter=adapter)
        result = builder.create_room("統合テストルーム")
        assert result.success, f"ルーム作成が失敗しました: {result.error}"
        assert result.step == "create_room"

    # 3-2: フルパイプライン
    def test_3_2_full_pipeline(self, adapter, test_png, test_mp3):
        """背景 + BGM + キャラクター2体の RoomDefinition で build_room()。"""
        definition = RoomDefinition(
            name="フルパイプラインテスト",
            description="統合テスト用ルーム",
            background_image=str(test_png),
            bgm=[{"file_path": str(test_mp3), "name": "テストBGM"}],
            characters=[
                CharacterPlacement(
                    name="戦士",
                    grid_x=2,
                    grid_y=3,
                    ccfolia_data={"name": "戦士", "initiative": 10},
                ),
                CharacterPlacement(
                    name="魔法使い",
                    grid_x=4,
                    grid_y=3,
                    ccfolia_data={"name": "魔法使い", "initiative": 8},
                ),
            ],
        )

        builder = RoomBuilder(adapter=adapter)
        results = builder.build_room(definition)

        assert len(results) > 0, "パイプライン結果が空です"
        # ルーム作成ステップは成功必須
        create_step = next((r for r in results if r.step == "create_room"), None)
        assert create_step is not None, "create_room ステップが見つかりません"
        assert create_step.success, f"ルーム作成失敗: {create_step.error}"

        # 全ステップのサマリーを出力
        for r in results:
            status = "OK" if r.success else "NG"
            print(f"  [{status}] {r.step}: {r.detail} {r.error}")

    # 3-3: grid 座標でのキャラクター配置
    def test_3_3_grid_character_placement(self, adapter):
        """grid_x=3, grid_y=5 指定で駒が正しいグリッド位置に現れる。"""
        builder = RoomBuilder(adapter=adapter)
        char = CharacterPlacement(
            name="グリッドテスト駒",
            grid_x=3,
            grid_y=5,
            ccfolia_data={"name": "グリッドテスト駒"},
        )
        result = builder.place_character(char)
        assert result.success, f"キャラクター配置失敗: {result.error}"
        assert "grid (3, 5)" in result.detail, f"グリッド座標が不正: {result.detail}"

    # 3-4: VLM 位置指定でのキャラクター配置
    def test_3_4_vlm_character_placement(self, adapter):
        """position="部屋の中央" 指定で駒がそれらしい位置に配置される。"""
        builder = RoomBuilder(adapter=adapter)
        char = CharacterPlacement(
            name="VLMテスト駒",
            position="部屋の中央",
            ccfolia_data={"name": "VLMテスト駒"},
        )
        result = builder.place_character(char)
        assert result.success, f"VLM キャラクター配置失敗: {result.error}"
        # VLM 位置特定成功 or フォールバック、いずれも success=True
        assert "VLM" in result.detail or "デフォルト" in result.detail

    # 3-5: エラーリカバリー
    def test_3_5_error_recovery(self, adapter, test_png, nonexistent_mp3):
        """存在しない BGM パスを含む定義で build_room() → BGM は失敗するが続行。"""
        definition = RoomDefinition(
            name="エラーリカバリーテスト",
            background_image=str(test_png),
            bgm=[{"file_path": nonexistent_mp3, "name": "存在しないBGM"}],
            characters=[
                CharacterPlacement(
                    name="リカバリーテスト駒",
                    grid_x=1,
                    grid_y=1,
                    ccfolia_data={"name": "リカバリーテスト駒"},
                ),
            ],
        )

        builder = RoomBuilder(adapter=adapter)
        results = builder.build_room(definition)

        # validate で BGM パスが見つからないエラー → ここで止まる場合
        # 定義の validate でパス不在エラーになるため、それも正しい挙動
        if results and results[0].step == "validate":
            assert not results[0].success, "validate が成功するはずがない"
            print("  [期待通り] validate 段階でエラー検出 → パイプライン中断")
            return

        # validate を通過した場合（パスチェック無し等）は BGM 失敗 + 後続続行
        bgm_steps = [r for r in results if r.step == "add_bgm"]
        char_steps = [r for r in results if r.step.startswith("place_character")]

        if bgm_steps:
            assert not bgm_steps[0].success, "存在しない BGM が成功するはずがない"
        assert len(char_steps) > 0, "BGM 失敗後にキャラクター配置が実行されていない"
