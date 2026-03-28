"""
test_room_builder.py — RoomBuilder のユニットテスト

RoomDefinition バリデーション、インクリメンタル API、
フルパイプライン、進捗コールバックをテストする。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.room_builder import (
    CharacterPlacement,
    RoomBuilder,
    RoomDefinition,
    StepResult,
)

# ──────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture()
def mock_adapter():
    adapter = MagicMock()
    # create_room returns AgentTaskResult-like object
    result_ok = MagicMock()
    result_ok.success = True
    result_ok.error = ""
    adapter.create_room.return_value = result_ok
    adapter.set_background.return_value = result_ok
    adapter.switch_bgm.return_value = result_ok
    adapter.upload_asset.return_value = "https://firebasestorage.example.com/asset123"
    adapter.spawn_piece.return_value = True
    adapter.move_piece.return_value = True
    adapter.get_vision_controller.return_value = MagicMock()
    return adapter


@pytest.fixture()
def builder(mock_adapter):
    return RoomBuilder(adapter=mock_adapter)


# ──────────────────────────────────────────
# RoomDefinition
# ──────────────────────────────────────────


class TestRoomDefinition:
    def test_from_dict_minimal(self):
        defn = RoomDefinition.from_dict({"name": "テストルーム"})
        assert defn.name == "テストルーム"
        assert defn.characters == []
        assert defn.bgm == []
        assert defn.background_image == ""

    def test_from_dict_full(self, tmp_path):
        img = tmp_path / "bg.png"
        img.write_bytes(b"\x89PNG")
        bgm = tmp_path / "music.mp3"
        bgm.write_bytes(b"\xFF\xFB")

        data = {
            "name": "ダンジョン",
            "description": "暗い洞窟",
            "background_image": str(img),
            "bgm": [{"file_path": str(bgm), "name": "洞窟BGM"}],
            "characters": [
                {"name": "戦士", "position": "入口", "ccfolia_data": {"name": "戦士"}},
                {"name": "魔法使い", "grid_x": 3, "grid_y": 5},
            ],
        }
        defn = RoomDefinition.from_dict(data)
        assert defn.name == "ダンジョン"
        assert defn.description == "暗い洞窟"
        assert len(defn.characters) == 2
        assert defn.characters[0].position == "入口"
        assert defn.characters[1].grid_x == 3

    def test_validate_missing_name(self):
        defn = RoomDefinition(name="")
        errors = defn.validate()
        assert any("ルーム名" in e for e in errors)

    def test_validate_missing_files(self):
        defn = RoomDefinition(
            name="test",
            background_image="/nonexistent/bg.png",
            bgm=[{"file_path": "/nonexistent/music.mp3"}],
            characters=[
                CharacterPlacement(name="c1", image_path="/nonexistent/token.png"),
            ],
        )
        errors = defn.validate()
        assert len(errors) == 3  # bg + bgm + token

    def test_validate_valid(self, tmp_path):
        img = tmp_path / "bg.png"
        img.write_bytes(b"\x89PNG")
        defn = RoomDefinition(name="ok", background_image=str(img))
        assert defn.validate() == []

    def test_validate_empty_character_name(self):
        defn = RoomDefinition(
            name="test",
            characters=[CharacterPlacement(name="")],
        )
        errors = defn.validate()
        assert any("キャラクター名" in e for e in errors)


# ──────────────────────────────────────────
# CharacterPlacement
# ──────────────────────────────────────────


class TestCharacterPlacement:
    def test_from_dict(self):
        cp = CharacterPlacement.from_dict({
            "name": "騎士",
            "image_path": "/path/to/knight.png",
            "position": "城門の前",
            "ccfolia_data": {"name": "騎士", "hp": 30},
        })
        assert cp.name == "騎士"
        assert cp.position == "城門の前"
        assert cp.ccfolia_data["hp"] == 30

    def test_from_dict_defaults(self):
        cp = CharacterPlacement.from_dict({"name": "X"})
        assert cp.image_path == ""
        assert cp.grid_x is None
        assert cp.grid_y is None

    def test_from_dict_with_grid(self):
        cp = CharacterPlacement.from_dict({"name": "A", "grid_x": 5, "grid_y": 10})
        assert cp.grid_x == 5
        assert cp.grid_y == 10


# ──────────────────────────────────────────
# StepResult
# ──────────────────────────────────────────


class TestStepResult:
    def test_defaults(self):
        r = StepResult(step="test", success=True)
        assert r.detail == ""
        assert r.error == ""

    def test_with_error(self):
        r = StepResult(step="fail", success=False, error="タイムアウト")
        assert r.error == "タイムアウト"


# ──────────────────────────────────────────
# インクリメンタル API
# ──────────────────────────────────────────


class TestIncrementalAPI:
    def test_create_room_success(self, builder, mock_adapter):
        r = builder.create_room("新しいルーム")
        assert r.success is True
        mock_adapter.create_room.assert_called_once_with("新しいルーム")

    def test_create_room_failure(self, builder, mock_adapter):
        mock_adapter.create_room.return_value.success = False
        mock_adapter.create_room.return_value.error = "接続エラー"
        r = builder.create_room("失敗ルーム")
        assert r.success is False
        assert "接続エラー" in r.error

    def test_set_background_uploads_and_applies(self, builder, mock_adapter):
        r = builder.set_background("/path/to/bg.png")
        assert r.success is True
        mock_adapter.upload_asset.assert_called_once_with("/path/to/bg.png", "background")
        mock_adapter.set_background.assert_called_once()

    def test_set_background_upload_failure(self, builder, mock_adapter):
        mock_adapter.upload_asset.return_value = None
        r = builder.set_background("/path/to/bg.png")
        assert r.success is False
        mock_adapter.set_background.assert_not_called()

    def test_add_bgm_success(self, builder, mock_adapter):
        r = builder.add_bgm("/path/to/music.mp3", "battle_bgm")
        assert r.success is True
        mock_adapter.upload_asset.assert_called_once_with("/path/to/music.mp3", "bgm")
        mock_adapter.switch_bgm.assert_called_once_with("battle_bgm")

    def test_add_bgm_uses_stem_when_no_name(self, builder, mock_adapter):
        r = builder.add_bgm("/path/to/ambient.ogg")
        assert r.success is True
        mock_adapter.switch_bgm.assert_called_once_with("ambient")

    def test_place_character_with_grid(self, builder, mock_adapter):
        char = CharacterPlacement(
            name="戦士", grid_x=3, grid_y=5,
            ccfolia_data={"name": "戦士"},
        )
        r = builder.place_character(char)
        assert r.success is True
        mock_adapter.spawn_piece.assert_called_once_with({"name": "戦士"})
        mock_adapter.move_piece.assert_called_once_with("戦士", 3, 5)

    def test_place_character_with_position_uses_vlm(self, builder, mock_adapter):
        mock_vision = MagicMock()
        mock_vision.find_empty_space.return_value = (240, 336)
        mock_adapter.get_vision_controller.return_value = mock_vision

        char = CharacterPlacement(
            name="魔法使い", position="十字路の近く",
            ccfolia_data={"name": "魔法使い"},
        )
        r = builder.place_character(char)
        assert r.success is True
        mock_vision.find_empty_space.assert_called_once_with(near="十字路の近く")

    def test_place_character_spawn_failure(self, builder, mock_adapter):
        mock_adapter.spawn_piece.return_value = False
        char = CharacterPlacement(name="失敗", ccfolia_data={"name": "失敗"})
        r = builder.place_character(char)
        assert r.success is False

    def test_place_character_default_position(self, builder, mock_adapter):
        char = CharacterPlacement(name="NPC", ccfolia_data={"name": "NPC"})
        r = builder.place_character(char)
        assert r.success is True
        assert "デフォルト" in r.detail

    def test_place_character_no_ccfolia_data(self, builder, mock_adapter):
        """ccfolia_data 未指定でもキャラクター名で駒を生成する。"""
        char = CharacterPlacement(name="テスト")
        r = builder.place_character(char)
        assert r.success is True
        mock_adapter.spawn_piece.assert_called_once_with({"name": "テスト"})

    def test_place_character_with_token_upload(self, builder, mock_adapter):
        char = CharacterPlacement(
            name="弓兵", image_path="/path/to/archer.png",
            ccfolia_data={"name": "弓兵"},
        )
        builder.place_character(char)
        mock_adapter.upload_asset.assert_called_once_with("/path/to/archer.png", "token")


# ──────────────────────────────────────────
# フルパイプライン
# ──────────────────────────────────────────


class TestBuildRoom:
    def test_full_success(self, builder, mock_adapter, tmp_path):
        img = tmp_path / "bg.png"
        img.write_bytes(b"\x89PNG")
        bgm = tmp_path / "music.mp3"
        bgm.write_bytes(b"\xFF\xFB")

        defn = RoomDefinition(
            name="テストルーム",
            background_image=str(img),
            bgm=[{"file_path": str(bgm), "name": "BGM"}],
            characters=[
                CharacterPlacement(name="A", ccfolia_data={"name": "A"}, grid_x=1, grid_y=2),
            ],
        )
        results = builder.build_room(defn)

        successes = [r for r in results if r.success]
        assert len(successes) == len(results)
        steps = [r.step for r in results]
        assert "create_room" in steps
        assert "set_background" in steps
        assert "add_bgm" in steps
        assert "place_character:A" in steps

    def test_stops_on_room_creation_failure(self, builder, mock_adapter):
        mock_adapter.create_room.return_value.success = False
        defn = RoomDefinition(
            name="失敗ルーム",
            characters=[CharacterPlacement(name="X", ccfolia_data={"name": "X"})],
        )
        results = builder.build_room(defn)
        assert len(results) == 1
        assert results[0].step == "create_room"
        assert results[0].success is False

    def test_continues_on_background_failure(self, builder, mock_adapter, tmp_path):
        img = tmp_path / "bg.png"
        img.write_bytes(b"\x89PNG")
        mock_adapter.upload_asset.return_value = None  # background upload fails

        defn = RoomDefinition(
            name="ルーム",
            background_image=str(img),
            characters=[CharacterPlacement(name="A", ccfolia_data={"name": "A"})],
        )
        results = builder.build_room(defn)

        steps = [r.step for r in results]
        assert "create_room" in steps
        assert "set_background" in steps
        assert "place_character:A" in steps  # continued despite bg failure

    def test_continues_on_character_failure(self, builder, mock_adapter):
        mock_adapter.spawn_piece.side_effect = [False, True]

        defn = RoomDefinition(
            name="ルーム",
            characters=[
                CharacterPlacement(name="Fail", ccfolia_data={"name": "Fail"}),
                CharacterPlacement(name="OK", ccfolia_data={"name": "OK"}),
            ],
        )
        results = builder.build_room(defn)

        char_results = [r for r in results if r.step.startswith("place_character")]
        assert len(char_results) == 2
        assert char_results[0].success is False
        assert char_results[1].success is True

    def test_empty_definition(self, builder):
        defn = RoomDefinition(name="空ルーム")
        results = builder.build_room(defn)
        assert len(results) == 1  # create_room only
        assert results[0].success is True

    def test_validation_failure(self, builder):
        defn = RoomDefinition(name="")
        results = builder.build_room(defn)
        assert len(results) == 1
        assert results[0].step == "validate"
        assert results[0].success is False


# ──────────────────────────────────────────
# 進捗コールバック
# ──────────────────────────────────────────


class TestProgressCallback:
    def test_callback_called_for_each_step(self, mock_adapter):
        progress_log: list[StepResult] = []
        builder = RoomBuilder(adapter=mock_adapter, on_progress=progress_log.append)

        defn = RoomDefinition(
            name="コールバックテスト",
            characters=[CharacterPlacement(name="A", ccfolia_data={"name": "A"})],
        )
        results = builder.build_room(defn)
        assert len(progress_log) == len(results)

    def test_no_callback_does_not_error(self, mock_adapter):
        builder = RoomBuilder(adapter=mock_adapter, on_progress=None)
        defn = RoomDefinition(name="テスト")
        results = builder.build_room(defn)
        assert len(results) >= 1

    def test_callback_receives_step_names(self, mock_adapter):
        steps: list[str] = []
        builder = RoomBuilder(
            adapter=mock_adapter,
            on_progress=lambda r: steps.append(r.step),
        )
        defn = RoomDefinition(name="X")
        builder.build_room(defn)
        assert "create_room" in steps


# ──────────────────────────────────────────
# results プロパティ
# ──────────────────────────────────────────


class TestResultsProperty:
    def test_results_is_copy(self, builder):
        defn = RoomDefinition(name="テスト")
        builder.build_room(defn)
        results = builder.results
        results.clear()
        assert len(builder.results) >= 1

    def test_results_reset_on_new_build(self, builder):
        builder.build_room(RoomDefinition(name="A"))
        first_count = len(builder.results)
        builder.build_room(RoomDefinition(name="B"))
        assert len(builder.results) == first_count  # same structure, same count
