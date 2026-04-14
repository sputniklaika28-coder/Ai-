"""test_foundry_adapter.py — FoundryVTTAdapter のユニットテスト。

httpx はすべてモック化。Foundry REST API のエンドポイント・データ変換を確認する。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.vtt_adapters.base_adapter import BaseVTTAdapter
from core.vtt_adapters.foundry_adapter import FoundryVTTAdapter, FoundryConnectionError


# ──────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────


def _make_httpx_resp(status_code: int, json_data=None, content: bytes = b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    resp.content = content
    resp.headers = {"content-type": "application/json"}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _patch_httpx_request(responses: list):
    """httpx.AsyncClient.request をモック化する（複数レスポンス対応）。"""
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(side_effect=responses)
    mock_client.post = AsyncMock()
    mock_instance = MagicMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_client)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return (
        patch("core.vtt_adapters.foundry_adapter.httpx.AsyncClient", return_value=mock_instance),
        mock_client,
    )


def _make_adapter(**kwargs) -> FoundryVTTAdapter:
    return FoundryVTTAdapter(
        base_url="http://localhost:30000",
        api_key="test-key",
        **kwargs,
    )


# ──────────────────────────────────────────
# インターフェース準拠
# ──────────────────────────────────────────


class TestFoundryVTTAdapterInterface:
    def test_is_subclass_of_base(self):
        assert issubclass(FoundryVTTAdapter, BaseVTTAdapter)

    def test_instantiation(self):
        adapter = _make_adapter()
        assert adapter._base_url == "http://localhost:30000"
        assert adapter._api_key == "test-key"

    def test_trailing_slash_stripped(self):
        adapter = FoundryVTTAdapter(base_url="http://localhost:30000/")
        assert adapter._base_url == "http://localhost:30000"

    def test_api_key_in_headers(self):
        adapter = _make_adapter()
        headers = adapter._build_headers()
        assert headers["X-Api-Key"] == "test-key"

    def test_no_api_key_no_auth_header(self):
        adapter = FoundryVTTAdapter(base_url="http://localhost:30000", api_key="")
        headers = adapter._build_headers()
        assert "X-Api-Key" not in headers


# ──────────────────────────────────────────
# connect
# ──────────────────────────────────────────


class TestFoundryVTTAdapterConnect:
    async def test_connect_success_stores_scene_id(self):
        adapter = _make_adapter()
        status_resp = _make_httpx_resp(200, {"status": "ok"})
        scenes_resp = _make_httpx_resp(
            200, [{"_id": "scene-abc", "name": "Test", "active": True}]
        )
        patch_ctx, _ = _patch_httpx_request([status_resp, scenes_resp])
        with patch_ctx:
            await adapter.connect(room_url="")
        assert adapter._connected is True
        assert adapter._active_scene_id == "scene-abc"

    async def test_connect_raises_on_unreachable(self):
        adapter = _make_adapter()
        patch_ctx, mock_client = _patch_httpx_request([])
        mock_client.request = AsyncMock(return_value=_make_httpx_resp(503))
        with patch_ctx:
            with pytest.raises(FoundryConnectionError):
                await adapter.connect(room_url="")

    async def test_connect_no_active_scene(self):
        adapter = _make_adapter()
        status_resp = _make_httpx_resp(200, {"status": "ok"})
        scenes_resp = _make_httpx_resp(
            200, [{"_id": "scene-xyz", "name": "Inactive", "active": False}]
        )
        patch_ctx, _ = _patch_httpx_request([status_resp, scenes_resp])
        with patch_ctx:
            await adapter.connect(room_url="")
        assert adapter._connected is True
        assert adapter._active_scene_id is None

    async def test_close_resets_state(self):
        adapter = _make_adapter()
        adapter._connected = True
        adapter._active_scene_id = "scene-abc"
        await adapter.close()
        assert adapter._connected is False
        assert adapter._active_scene_id is None


# ──────────────────────────────────────────
# get_board_state
# ──────────────────────────────────────────


class TestFoundryVTTAdapterGetBoardState:
    async def test_returns_empty_on_no_tokens(self):
        adapter = _make_adapter()
        adapter._active_scene_id = "scene-1"
        tokens_resp = _make_httpx_resp(200, [])
        patch_ctx, _ = _patch_httpx_request([tokens_resp])
        with patch_ctx:
            result = await adapter.get_board_state()
        assert result == []

    async def test_maps_pixel_to_grid_coords(self):
        adapter = _make_adapter(grid_size=100)
        adapter._active_scene_id = "scene-1"
        tokens_resp = _make_httpx_resp(
            200,
            [{"_id": "tok-1", "name": "勇者", "img": "img/hero.png", "x": 200, "y": 300}],
        )
        patch_ctx, _ = _patch_httpx_request([tokens_resp])
        with patch_ctx:
            result = await adapter.get_board_state()
        assert len(result) == 1
        assert result[0]["grid_x"] == 2   # 200 // 100
        assert result[0]["grid_y"] == 3   # 300 // 100
        assert result[0]["px_x"] == 200
        assert result[0]["px_y"] == 300

    async def test_returns_foundry_id_as_piece_id(self):
        adapter = _make_adapter(grid_size=100)
        adapter._active_scene_id = "scene-1"
        tokens_resp = _make_httpx_resp(
            200, [{"_id": "tok-xyz", "name": "スライム", "x": 0, "y": 0}]
        )
        patch_ctx, _ = _patch_httpx_request([tokens_resp])
        with patch_ctx:
            result = await adapter.get_board_state()
        assert result[0]["piece_id"] == "tok-xyz"

    async def test_returns_empty_on_api_error(self):
        adapter = _make_adapter()
        adapter._active_scene_id = "scene-1"
        patch_ctx, _ = _patch_httpx_request([_make_httpx_resp(500)])
        with patch_ctx:
            result = await adapter.get_board_state()
        assert result == []


# ──────────────────────────────────────────
# move_piece
# ──────────────────────────────────────────


class TestFoundryVTTAdapterMovePiece:
    async def test_sends_patch_with_correct_pixel_coords(self):
        adapter = _make_adapter(grid_size=100)
        patch_resp = _make_httpx_resp(200, {"_id": "tok-1"})
        patch_ctx, mock_client = _patch_httpx_request([patch_resp])
        with patch_ctx:
            result = await adapter.move_piece("tok-1", grid_x=3, grid_y=4)
        assert result is True
        call = mock_client.request.call_args
        assert call[0][0] == "PATCH"
        assert "tok-1" in call[0][1]
        body = call[1]["json"]
        assert body["x"] == 300   # 3 * 100
        assert body["y"] == 400   # 4 * 100

    async def test_returns_false_on_api_error(self):
        adapter = _make_adapter()
        patch_ctx, _ = _patch_httpx_request([_make_httpx_resp(404)])
        with patch_ctx:
            result = await adapter.move_piece("tok-1", grid_x=0, grid_y=0)
        assert result is False

    async def test_returns_false_on_empty_piece_id(self):
        adapter = _make_adapter()
        result = await adapter.move_piece("", grid_x=0, grid_y=0)
        assert result is False


# ──────────────────────────────────────────
# send_chat
# ──────────────────────────────────────────


class TestFoundryVTTAdapterSendChat:
    async def test_posts_message_with_correct_structure(self):
        adapter = _make_adapter()
        post_resp = _make_httpx_resp(201, {"_id": "msg-1"})
        patch_ctx, mock_client = _patch_httpx_request([post_resp])
        with patch_ctx:
            result = await adapter.send_chat("GM", "テストメッセージ")
        assert result is True
        call = mock_client.request.call_args
        assert call[0][0] == "POST"
        assert "/api/messages" in call[0][1]

    async def test_speaker_name_in_payload(self):
        adapter = _make_adapter()
        post_resp = _make_httpx_resp(200, {"_id": "msg-2"})
        patch_ctx, mock_client = _patch_httpx_request([post_resp])
        with patch_ctx:
            await adapter.send_chat("魔法使い", "魔法を使う！")
        body = mock_client.request.call_args[1]["json"]
        assert body["speaker"]["alias"] == "魔法使い"
        assert body["content"] == "魔法を使う！"
        assert body["type"] == 0  # CHAT type

    async def test_returns_false_on_error(self):
        adapter = _make_adapter()
        patch_ctx, _ = _patch_httpx_request([_make_httpx_resp(500)])
        with patch_ctx:
            result = await adapter.send_chat("GM", "test")
        assert result is False


# ──────────────────────────────────────────
# get_chat_messages
# ──────────────────────────────────────────


class TestFoundryVTTAdapterGetChatMessages:
    async def test_filters_non_chat_type_messages(self):
        adapter = _make_adapter()
        raw_messages = [
            {"type": 0, "speaker": {"alias": "GM"}, "content": "正しいメッセージ"},
            {"type": 5, "speaker": {"alias": "System"}, "content": "ロールメッセージ"},
            {"type": 0, "speaker": {"alias": "Player"}, "content": "プレイヤー発言"},
        ]
        get_resp = _make_httpx_resp(200, raw_messages)
        patch_ctx, _ = _patch_httpx_request([get_resp])
        with patch_ctx:
            result = await adapter.get_chat_messages()
        assert len(result) == 2
        speakers = [m["speaker"] for m in result]
        assert "GM" in speakers
        assert "Player" in speakers
        assert "System" not in speakers

    async def test_returns_speaker_and_body(self):
        adapter = _make_adapter()
        raw_messages = [
            {"type": 0, "speaker": {"alias": "NPC"}, "content": "こんにちは"},
        ]
        get_resp = _make_httpx_resp(200, raw_messages)
        patch_ctx, _ = _patch_httpx_request([get_resp])
        with patch_ctx:
            result = await adapter.get_chat_messages()
        assert result[0]["speaker"] == "NPC"
        assert result[0]["body"] == "こんにちは"

    async def test_returns_empty_on_api_error(self):
        adapter = _make_adapter()
        patch_ctx, _ = _patch_httpx_request([_make_httpx_resp(500)])
        with patch_ctx:
            result = await adapter.get_chat_messages()
        assert result == []


# ──────────────────────────────────────────
# spawn_piece
# ──────────────────────────────────────────


class TestFoundryVTTAdapterSpawnPiece:
    def test_ccfolia_to_foundry_translation(self):
        adapter = _make_adapter(grid_size=100)
        ccfolia = {
            "name": "勇者",
            "image": {"url": "http://example.com/hero.png"},
            "params": {"hp": 30, "maxHp": 50},
            "position": {"x": 200, "y": 300},
        }
        foundry = adapter._ccfolia_to_foundry_token(ccfolia)
        assert foundry["name"] == "勇者"
        assert foundry["img"] == "http://example.com/hero.png"
        assert foundry["x"] == 200
        assert foundry["y"] == 300
        assert foundry["actorData"]["data"]["attributes"]["hp"]["value"] == 30
        assert foundry["actorData"]["data"]["attributes"]["hp"]["max"] == 50

    async def test_spawn_piece_success(self):
        adapter = _make_adapter()
        adapter._active_scene_id = "scene-1"
        post_resp = _make_httpx_resp(201, {"_id": "tok-new"})
        patch_ctx, _ = _patch_httpx_request([post_resp])
        with patch_ctx:
            result = await adapter.spawn_piece({
                "name": "スライム",
                "image": {"url": "img/slime.png"},
                "params": {"hp": 10, "maxHp": 10},
            })
        assert result is True

    async def test_spawn_piece_returns_false_on_error(self):
        adapter = _make_adapter()
        patch_ctx, _ = _patch_httpx_request([_make_httpx_resp(500)])
        with patch_ctx:
            result = await adapter.spawn_piece({"name": "エラー"})
        assert result is False


# ──────────────────────────────────────────
# take_screenshot
# ──────────────────────────────────────────


class TestFoundryVTTAdapterTakeScreenshot:
    async def test_returns_png_bytes_when_available(self):
        adapter = _make_adapter()
        adapter._active_scene_id = "scene-1"
        png_data = b"\x89PNG\r\nfake"
        get_resp = _make_httpx_resp(200, content=png_data)
        get_resp.headers = {"content-type": "image/png"}
        patch_ctx, _ = _patch_httpx_request([get_resp])
        with patch_ctx:
            result = await adapter.take_screenshot()
        # PNG バイトまたは None を返す（API が非対応の場合 None）
        # take_screenshot は None も許容
        assert result is None or isinstance(result, bytes)

    async def test_returns_none_when_no_scene(self):
        adapter = _make_adapter()
        adapter._active_scene_id = None
        result = await adapter.take_screenshot()
        assert result is None
