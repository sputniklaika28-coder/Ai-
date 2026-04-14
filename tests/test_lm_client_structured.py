"""test_lm_client_structured.py — LMClient.generate_structured() のユニットテスト。

httpx はすべてモック化。Pydantic モデルの生成・バリデーションを確認する。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.lm_client import LMClient
from core.schemas import ChatPostAction, SingleCoordinate, MemorySummary


# ──────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────


def _make_chat_resp(content: str, finish_reason: str = "stop"):
    """OpenAI 互換レスポンス dict を作る。"""
    return {
        "choices": [
            {
                "message": {"content": content, "tool_calls": None},
                "finish_reason": finish_reason,
            }
        ]
    }


def _make_httpx_resp(status_code: int, json_data=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"content-type": "application/json"}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _patch_httpx(get_resp, post_resp):
    """GET（is_server_running）と POST（補完）両方をモック化する。"""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=get_resp)
    mock_client.post = AsyncMock(return_value=post_resp)
    mock_instance = MagicMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_client)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return patch("core.lm_client.httpx.AsyncClient", return_value=mock_instance), mock_client


# ──────────────────────────────────────────
# generate_structured
# ──────────────────────────────────────────


class TestGenerateStructured:
    async def test_returns_none_when_server_down(self):
        client = LMClient()
        server_down = _make_httpx_resp(500)
        patch_ctx, _ = _patch_httpx(server_down, None)
        with patch_ctx:
            result = await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
            )
        assert result is None

    async def test_returns_pydantic_model_on_valid_json(self):
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        payload = _make_chat_resp('{"character_name": "GM", "text": "ゲーム開始"}')
        post_resp = _make_httpx_resp(200, payload)
        patch_ctx, _ = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            result = await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
            )
        assert isinstance(result, ChatPostAction)
        assert result.character_name == "GM"
        assert result.text == "ゲーム開始"

    async def test_returns_none_on_invalid_json(self):
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        payload = _make_chat_resp("これは JSON ではありません")
        post_resp = _make_httpx_resp(200, payload)
        patch_ctx, _ = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            result = await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
            )
        assert result is None

    async def test_returns_none_on_schema_mismatch(self):
        """JSON は valid だが Pydantic スキーマに合わない場合。"""
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        payload = _make_chat_resp('{"wrong_field": "value"}')
        post_resp = _make_httpx_resp(200, payload)
        patch_ctx, _ = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            result = await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
            )
        assert result is None

    async def test_sends_json_schema_in_payload(self):
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        payload = _make_chat_resp(
            '{"px_x": 100, "px_y": 200, "found": true}'
        )
        post_resp = _make_httpx_resp(200, payload)
        patch_ctx, mock_client = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=SingleCoordinate,
            )
        call_kwargs = mock_client.post.call_args
        sent_json = call_kwargs[1]["json"]
        assert sent_json["response_format"]["type"] == "json_schema"
        assert sent_json["response_format"]["json_schema"]["name"] == "SingleCoordinate"
        assert "schema" in sent_json["response_format"]["json_schema"]

    async def test_strict_mode_in_payload(self):
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        payload = _make_chat_resp('{"character_name": "A", "text": "B"}')
        post_resp = _make_httpx_resp(200, payload)
        patch_ctx, mock_client = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
                strict=True,
            )
        sent_json = mock_client.post.call_args[1]["json"]
        assert sent_json["response_format"]["json_schema"]["strict"] is True

    async def test_fallback_from_reasoning_content(self):
        """content が空で reasoning_content に JSON がある場合のフォールバック。"""
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        api_resp = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": (
                            "考え中...\n"
                            '{"character_name": "NPC", "text": "こんにちは"}\n'
                            "以上が出力です。"
                        ),
                        "tool_calls": None,
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        post_resp = _make_httpx_resp(200, api_resp)
        patch_ctx, _ = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            result = await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
            )
        assert isinstance(result, ChatPostAction)
        assert result.character_name == "NPC"

    async def test_returns_none_on_http_error(self):
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        post_resp = _make_httpx_resp(400, text="Bad Request")
        patch_ctx, _ = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            result = await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
            )
        assert result is None

    async def test_temperature_passed_to_payload(self):
        client = LMClient()
        server_ok = _make_httpx_resp(200)
        payload = _make_chat_resp('{"summary": "テスト", "key_events": [], "active_characters": []}')
        post_resp = _make_httpx_resp(200, payload)
        patch_ctx, mock_client = _patch_httpx(server_ok, post_resp)
        with patch_ctx:
            await client.generate_structured(
                system_prompt="test",
                user_message="test",
                schema=MemorySummary,
                temperature=0.1,
            )
        sent_json = mock_client.post.call_args[1]["json"]
        assert sent_json["temperature"] == 0.1

    def test_sync_wrapper_returns_model(self):
        """generate_structured_sync が asyncio.run() 経由で動作することを確認。"""
        client = LMClient()

        async def _fake_structured(*args, **kwargs):
            return ChatPostAction(character_name="X", text="Y")

        with patch.object(client, "generate_structured", side_effect=_fake_structured):
            result = client.generate_structured_sync(
                system_prompt="test",
                user_message="test",
                schema=ChatPostAction,
            )
        assert isinstance(result, ChatPostAction)
        assert result.character_name == "X"
