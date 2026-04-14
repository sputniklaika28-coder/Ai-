"""test_schemas.py — core.schemas Pydantic モデルのユニットテスト。"""

import pytest
from pydantic import ValidationError

from core.schemas import (
    BoardAnalysisResult,
    ChatLogResult,
    ChatMessage,
    ChatPostAction,
    GameIntention,
    MemorySummary,
    NarrativeAction,
    PieceLocation,
    SingleCoordinate,
    VisionCoordinate,
    VisionCoordinateList,
    VTTActionPlan,
)


# ──────────────────────────────────────────
# ChatPostAction
# ──────────────────────────────────────────


class TestChatPostAction:
    def test_valid(self):
        obj = ChatPostAction(character_name="GM", text="ゲーム開始")
        assert obj.character_name == "GM"
        assert obj.text == "ゲーム開始"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            ChatPostAction(character_name="GM")  # text が必須

    def test_round_trip_json(self):
        obj = ChatPostAction(character_name="敵A", text="攻撃！")
        restored = ChatPostAction.model_validate_json(obj.model_dump_json())
        assert restored == obj

    def test_json_schema_has_descriptions(self):
        schema = ChatPostAction.model_json_schema()
        props = schema["properties"]
        assert props["character_name"]["description"] != ""
        assert props["text"]["description"] != ""


# ──────────────────────────────────────────
# GameIntention
# ──────────────────────────────────────────


class TestGameIntention:
    def test_valid_minimal(self):
        obj = GameIntention(actor="勇者", action_type="attack")
        assert obj.actor == "勇者"
        assert obj.target is None
        assert obj.skill_name is None

    def test_valid_full(self):
        obj = GameIntention(
            actor="魔法使い",
            target="スライム",
            action_type="skill",
            skill_name="ファイア",
            dialogue="燃え尽きろ！",
        )
        assert obj.target == "スライム"
        assert obj.skill_name == "ファイア"

    def test_missing_actor_raises(self):
        with pytest.raises(ValidationError):
            GameIntention(action_type="attack")


# ──────────────────────────────────────────
# MemorySummary
# ──────────────────────────────────────────


class TestMemorySummary:
    def test_valid(self):
        obj = MemorySummary(
            summary="パーティは森に到着した",
            key_events=["スライムを倒した", "宝箱を開けた"],
            active_characters=["勇者", "魔法使い"],
        )
        assert len(obj.key_events) == 2

    def test_defaults(self):
        obj = MemorySummary(summary="短いまとめ")
        assert obj.key_events == []
        assert obj.active_characters == []

    def test_missing_summary_raises(self):
        with pytest.raises(ValidationError):
            MemorySummary(key_events=["event"])


# ──────────────────────────────────────────
# BoardAnalysisResult / PieceLocation
# ──────────────────────────────────────────


class TestBoardAnalysisResult:
    def test_empty_pieces(self):
        obj = BoardAnalysisResult()
        assert obj.pieces == []

    def test_with_pieces(self):
        obj = BoardAnalysisResult(
            pieces=[PieceLocation(description="勇者", px_x=100, px_y=200)]
        )
        assert len(obj.pieces) == 1
        assert obj.pieces[0].px_x == 100

    def test_suggested_moves_default_empty(self):
        obj = BoardAnalysisResult()
        assert obj.suggested_moves == []

    def test_round_trip_json(self):
        obj = BoardAnalysisResult(
            pieces=[PieceLocation(description="スライム", px_x=50, px_y=60, confidence=0.9)]
        )
        restored = BoardAnalysisResult.model_validate_json(obj.model_dump_json())
        assert restored.pieces[0].description == "スライム"
        assert restored.pieces[0].confidence == 0.9


class TestPieceLocation:
    def test_valid(self):
        p = PieceLocation(description="剣士", px_x=10, px_y=20)
        assert p.confidence == 1.0

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            PieceLocation(description="剣士", px_x=10)  # px_y が必須


# ──────────────────────────────────────────
# SingleCoordinate
# ──────────────────────────────────────────


class TestSingleCoordinate:
    def test_found_default_true(self):
        obj = SingleCoordinate(px_x=300, px_y=400)
        assert obj.found is True

    def test_not_found(self):
        obj = SingleCoordinate(px_x=0, px_y=0, found=False)
        assert obj.found is False

    def test_missing_coords_raises(self):
        with pytest.raises(ValidationError):
            SingleCoordinate(px_x=100)  # px_y が必須


# ──────────────────────────────────────────
# ChatLogResult / ChatMessage
# ──────────────────────────────────────────


class TestChatLogResult:
    def test_empty_messages(self):
        obj = ChatLogResult()
        assert obj.messages == []

    def test_with_messages(self):
        obj = ChatLogResult(
            messages=[
                ChatMessage(speaker="GM", body="ゲーム開始"),
                ChatMessage(speaker="Player", body="移動します"),
            ]
        )
        assert len(obj.messages) == 2

    def test_round_trip_json(self):
        obj = ChatLogResult(
            messages=[ChatMessage(speaker="GM", body="テスト")]
        )
        restored = ChatLogResult.model_validate_json(obj.model_dump_json())
        assert restored.messages[0].speaker == "GM"


# ──────────────────────────────────────────
# VisionCoordinate / VisionCoordinateList
# ──────────────────────────────────────────


class TestVisionCoordinate:
    def test_defaults(self):
        obj = VisionCoordinate(px_x=100, px_y=200)
        assert obj.confidence == 1.0
        assert obj.label == ""

    def test_full(self):
        obj = VisionCoordinate(px_x=50, px_y=60, confidence=0.85, label="チャット入力欄")
        assert obj.label == "チャット入力欄"

    def test_json_schema_is_object(self):
        schema = VisionCoordinate.model_json_schema()
        assert schema.get("type") == "object"

    def test_coordinate_list(self):
        lst = VisionCoordinateList(
            items=[
                VisionCoordinate(px_x=10, px_y=20),
                VisionCoordinate(px_x=30, px_y=40, label="ボタン"),
            ]
        )
        assert len(lst.items) == 2


# ──────────────────────────────────────
# NarrativeAction
# ──────────────────────────────────────


class TestNarrativeAction:
    def test_valid(self):
        obj = NarrativeAction(
            narration="闇の中で足音が響く",
            chat_speaker="GM",
            chat_text="敵が現れた！",
        )
        assert obj.move_piece_id is None
        assert obj.move_grid_x is None

    def test_with_move(self):
        obj = NarrativeAction(
            narration="敵が移動する",
            chat_speaker="GM",
            chat_text="スライムが近づいてきた",
            move_piece_id="slime-tok",
            move_grid_x=5,
            move_grid_y=3,
        )
        assert obj.move_grid_x == 5

    def test_missing_required_raises(self):
        with pytest.raises(Exception):
            NarrativeAction(narration="test")  # chat_speaker, chat_text が必須


# ──────────────────────────────────────
# VTTActionPlan
# ──────────────────────────────────────


class TestVTTActionPlan:
    def test_valid_click(self):
        obj = VTTActionPlan(action="click", px_x=100, px_y=200)
        assert obj.action == "click"

    def test_valid_done(self):
        obj = VTTActionPlan(action="done")
        assert obj.action == "done"

    def test_invalid_action_raises(self):
        with pytest.raises(Exception):
            VTTActionPlan(action="unknown_action")

    def test_drag_fields(self):
        obj = VTTActionPlan(
            action="drag",
            px_x=10, px_y=20,
            drag_to_x=100, drag_to_y=200,
        )
        assert obj.drag_to_x == 100

    def test_json_schema_action_has_enum(self):
        schema = VTTActionPlan.model_json_schema()
        # action フィールドに enum 制約があること
        action_schema = schema["properties"]["action"]
        assert "enum" in action_schema or "$ref" in action_schema or "anyOf" in action_schema
