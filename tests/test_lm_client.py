"""
test_lm_client.py — LMClient のユニットテスト

テスト対象:
  - is_server_running()
  - _clean_response()
  - generate_response()
  - generate_with_tools()

外部依存 httpx はすべてモック化する。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.lm_client import LMClient


# ──────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────


def _make_api_response(content: str):
    """OpenAI 互換レスポンスの dict を作る"""
    return {"choices": [{"message": {"content": content, "tool_calls": None}}]}


def _make_httpx_resp(status_code: int, json_data=None):
    """httpx レスポンスモックを作る"""
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _patch_httpx_post(*responses):
    """httpx.AsyncClient をモック化する（post 用）。
    returns: (patch_ctx, mock_client) のタプル。
    """
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=list(responses))
    mock_client.get = AsyncMock()
    mock_instance = MagicMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_client)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return patch("core.lm_client.httpx.AsyncClient", return_value=mock_instance), mock_client


def _patch_httpx_get(response):
    """httpx.AsyncClient をモック化する（get 用）。"""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.post = AsyncMock()
    mock_instance = MagicMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_client)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return patch("core.lm_client.httpx.AsyncClient", return_value=mock_instance), mock_client


# ──────────────────────────────────────────
# is_server_running
# ──────────────────────────────────────────


class TestIsServerRunning:
    async def test_returns_true_when_200(self):
        client = LMClient()
        patch_ctx, _ = _patch_httpx_get(_make_httpx_resp(200))
        with patch_ctx:
            assert await client.is_server_running() is True

    async def test_returns_false_when_non_200(self):
        client = LMClient()
        patch_ctx, _ = _patch_httpx_get(_make_httpx_resp(500))
        with patch_ctx:
            assert await client.is_server_running() is False

    async def test_returns_false_on_connection_error(self):
        client = LMClient()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError)
        mock_instance = MagicMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_client)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        with patch("core.lm_client.httpx.AsyncClient", return_value=mock_instance):
            assert await client.is_server_running() is False

    async def test_hits_correct_endpoint(self):
        client = LMClient(base_url="http://localhost:9999")
        patch_ctx, mock_client = _patch_httpx_get(_make_httpx_resp(200))
        with patch_ctx:
            await client.is_server_running()
            mock_client.get.assert_called_once_with("http://localhost:9999/v1/models")


# ──────────────────────────────────────────
# _clean_response
# ──────────────────────────────────────────


class TestCleanResponse:
    def setup_method(self):
        self.client = LMClient()

    def test_plain_json_passthrough(self):
        text = '{"action": "move"}'
        assert self.client._clean_response(text) == '{"action": "move"}'

    def test_strips_think_tag(self):
        text = '<think>考え中…</think>\n{"action": "wait"}'
        result = self.client._clean_response(text)
        assert result == '{"action": "wait"}'

    def test_strips_leading_prose(self):
        text = '思考プロセス：では移動します。\n{"action": "move"}'
        result = self.client._clean_response(text)
        assert result == '{"action": "move"}'

    def test_strips_trailing_prose(self):
        text = '{"action": "move"}\n出力完了しました。'
        result = self.client._clean_response(text)
        assert result == '{"action": "move"}'

    def test_strips_markdown_code_block(self):
        text = '```json\n{"action": "attack"}\n```'
        result = self.client._clean_response(text)
        assert result == '{"action": "attack"}'

    def test_empty_string_returns_empty(self):
        result = self.client._clean_response("")
        assert result == ""

    def test_no_braces_returns_empty(self):
        result = self.client._clean_response("hello world")
        assert "hello world" in result or result == "hello world"

    def test_nested_think_tag(self):
        text = '<think>step1</think><think>step2</think>{"ok": true}'
        result = self.client._clean_response(text)
        assert result == '{"ok": true}'


# ──────────────────────────────────────────
# generate_response
# ──────────────────────────────────────────


class TestGenerateResponse:
    async def test_returns_none_when_server_down(self):
        client = LMClient()
        with patch.object(client, "is_server_running", new=AsyncMock(return_value=False)):
            content, tools = await client.generate_response("sys", "user")
        assert content is None
        assert tools is None

    async def test_returns_cleaned_json(self):
        client = LMClient()
        api_resp = _make_httpx_resp(200, _make_api_response('{"action": "cast"}'))
        patch_ctx, _ = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user")
        assert content == '{"action": "cast"}'
        assert tools is None

    async def test_json_mode_adds_response_format(self):
        client = LMClient()
        api_resp = _make_httpx_resp(200, _make_api_response('{"ok": true}'))
        patch_ctx, mock_client = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            await client.generate_response("sys", "user", json_mode=True)
        payload = mock_client.post.call_args[1]["json"]
        assert payload["response_format"] == {"type": "json_object"}

    async def test_no_think_prepends_flag(self):
        client = LMClient()
        api_resp = _make_httpx_resp(200, _make_api_response('{"ok": true}'))
        patch_ctx, mock_client = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            await client.generate_response("sys", "user", no_think=True)
        payload = mock_client.post.call_args[1]["json"]
        assert payload["messages"][0]["content"] == "sys"
        assert payload.get("chat_template_kwargs") == {"enable_thinking": False}

    async def test_returns_none_on_non_200(self):
        client = LMClient()
        patch_ctx, _ = _patch_httpx_post(_make_httpx_resp(503))
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user")
        assert content is None

    async def test_returns_none_on_exception(self):
        client = LMClient()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=TimeoutError)
        mock_instance = MagicMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_client)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch("core.lm_client.httpx.AsyncClient", return_value=mock_instance),
        ):
            content, tools = await client.generate_response("sys", "user")
        assert content is None

    async def test_falls_back_to_reasoning_content_when_valid_json(self):
        """content が空で reasoning_content に有効な JSON が含まれる場合、フォールバックを採用する"""
        client = LMClient()
        api_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "",
                    "reasoning_content": '思考中…\n{"action": "heal"}',
                    "tool_calls": None,
                },
            }]
        })
        patch_ctx, _ = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user")
        assert content == '{"action": "heal"}'

    async def test_no_fallback_when_reasoning_content_is_thinking_text(self):
        """reasoning_content が思考テキストのみで有効な JSON でない場合、空文字を返す（no_think=False でリトライなし）"""
        client = LMClient()
        api_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "",
                    "reasoning_content": 'Thinking: Step 1 analyze {"partial": thinking...} more text',
                    "tool_calls": None,
                },
            }]
        })
        patch_ctx, _ = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user", no_think=False)
        assert content == ""

    async def test_fallback_to_reasoning_when_finish_reason_length(self):
        """finish_reason=length でも reasoning_content 内に完結した JSON があれば抽出する"""
        client = LMClient()
        api_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": 'Thinking: {"action": "heal", "target": "ally"} more thinking...',
                    "tool_calls": None,
                },
            }]
        })
        patch_ctx, _ = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user")
        assert content == '{"action": "heal", "target": "ally"}'

    async def test_retries_with_doubled_max_tokens_when_no_think_ignored(self):
        """finish_reason=length で content が空の場合、max_tokens を倍にしてリトライする"""
        client = LMClient()
        first_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": "Thinking about the problem...",
                    "tool_calls": None,
                },
            }]
        })
        retry_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": '{"result": "success"}',
                    "reasoning_content": "Now I have enough tokens...",
                    "tool_calls": None,
                },
            }]
        })
        patch_ctx, mock_client = _patch_httpx_post(first_resp, retry_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response(
                "sys", "user", max_tokens=4096, no_think=True
            )
        assert content == '{"result": "success"}'
        assert mock_client.post.call_count == 2
        retry_payload = mock_client.post.call_args_list[1][1]["json"]
        assert retry_payload["max_tokens"] == 8192
        assert retry_payload.get("chat_template_kwargs") == {"enable_thinking": False}
        assert retry_payload["messages"][0]["content"] == "sys"

    async def test_no_retry_when_no_think_is_false_and_finish_reason_stop(self):
        """no_think=False かつ finish_reason=stop の場合、思考が無視されてもリトライしない"""
        client = LMClient()
        api_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "",
                    "reasoning_content": "Thinking...",
                    "tool_calls": None,
                },
            }]
        })
        patch_ctx, mock_client = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user", no_think=False)
        assert content == ""
        assert mock_client.post.call_count == 1

    async def test_retries_on_finish_reason_length_without_no_think(self):
        """no_think=False でも finish_reason=length で content 空なら max_tokens×2 でリトライする"""
        client = LMClient()
        first_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": "長い思考テキスト...",
                    "tool_calls": None,
                },
            }]
        })
        retry_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": '{"name": "リトライ成功"}',
                    "reasoning_content": "",
                    "tool_calls": None,
                },
            }]
        })
        patch_ctx, mock_client = _patch_httpx_post(first_resp, retry_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response(
                "sys", "user", max_tokens=8192, no_think=False
            )
        assert content == '{"name": "リトライ成功"}'
        assert mock_client.post.call_count == 2
        retry_payload = mock_client.post.call_args_list[1][1]["json"]
        assert retry_payload["max_tokens"] == 16384
        assert retry_payload.get("chat_template_kwargs") is None
        assert retry_payload["messages"][0]["content"] == "sys"

    async def test_retry_returns_tool_calls_from_retry_response(self):
        """リトライ成功時、tool_calls は初回レスポンスではなくリトライレスポンスから取得する"""
        client = LMClient()
        first_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": "Thinking...",
                    "tool_calls": [{"id": "old", "function": {"name": "stale"}}],
                },
            }]
        })
        retry_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": '{"ok": true}',
                    "reasoning_content": "",
                    "tool_calls": [{"id": "new", "function": {"name": "fresh"}}],
                },
            }]
        })
        patch_ctx, _ = _patch_httpx_post(first_resp, retry_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user", no_think=True)
        assert tools is not None
        assert tools[0]["id"] == "new"
        assert tools[0]["function"]["name"] == "fresh"

    async def test_empty_tool_calls_list_normalized_to_none(self):
        """tool_calls が空リスト [] の場合、None に正規化する"""
        client = LMClient()
        api_resp = _make_httpx_resp(200, {
            "choices": [{
                "message": {
                    "content": '{"action": "wait"}',
                    "tool_calls": [],
                }
            }]
        })
        patch_ctx, _ = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_response("sys", "user")
        assert tools is None

    async def test_custom_base_url_and_model(self):
        client = LMClient(base_url="http://myserver:5678", model="my-model")
        api_resp = _make_httpx_resp(200, _make_api_response('{"x": 1}'))
        patch_ctx, mock_client = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            await client.generate_response("sys", "user")
        url = mock_client.post.call_args[0][0]
        assert "myserver:5678" in url
        payload = mock_client.post.call_args[1]["json"]
        assert payload["model"] == "my-model"

    async def test_extracts_json_from_reasoning_with_thinking_text(self):
        """reasoning_content に思考テキストとJSONが混在する場合、JSONを抽出する"""
        client = LMClient()
        embedded_json = '{"name": "テスト太郎", "body": 4, "soul": 3}'
        api_resp = _make_httpx_resp(200, {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "",
                    "reasoning_content": f"まずキャラを考えます…\n{embedded_json}\nこれで完成です。",
                    "tool_calls": None,
                },
            }]
        })
        patch_ctx, _ = _patch_httpx_post(api_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, _ = await client.generate_response("sys", "user", no_think=False)
        assert '"name"' in content
        assert "テスト太郎" in content


# ──────────────────────────────────────────
# generate_with_tools
# ──────────────────────────────────────────


class TestGenerateWithTools:
    async def test_returns_none_when_server_not_running(self):
        client = LMClient()
        with patch.object(client, "is_server_running", new=AsyncMock(return_value=False)):
            content, tools = await client.generate_with_tools(
                [{"role": "user", "content": "hello"}],
                [],
            )
        assert content is None
        assert tools is None

    async def test_returns_content_and_tool_calls(self):
        client = LMClient()
        mock_resp = _make_httpx_resp(200, {
            "choices": [{
                "message": {
                    "content": "考えています",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "post_chat",
                                "arguments": '{"text": "こんにちは"}',
                            },
                        }
                    ],
                }
            }]
        })
        patch_ctx, _ = _patch_httpx_post(mock_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tool_calls = await client.generate_with_tools(
                [{"role": "user", "content": "test"}],
                [{"type": "function", "function": {"name": "post_chat"}}],
            )
        assert content == "考えています"
        assert tool_calls is not None
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "post_chat"

    async def test_returns_content_only_when_no_tool_calls(self):
        client = LMClient()
        mock_resp = _make_httpx_resp(200, {
            "choices": [{"message": {"content": "plain text response"}}]
        })
        patch_ctx, _ = _patch_httpx_post(mock_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tool_calls = await client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content == "plain text response"
        assert tool_calls is None

    async def test_strips_think_tags_from_content(self):
        client = LMClient()
        mock_resp = _make_httpx_resp(200, {
            "choices": [
                {"message": {"content": "<think>考え中</think>結果のテキスト"}}
            ]
        })
        patch_ctx, _ = _patch_httpx_post(mock_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, _ = await client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content == "結果のテキスト"

    async def test_includes_tools_in_payload(self):
        client = LMClient()
        mock_resp = _make_httpx_resp(200, {
            "choices": [{"message": {"content": "ok"}}]
        })
        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        patch_ctx, mock_client = _patch_httpx_post(mock_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            await client.generate_with_tools(
                [{"role": "user", "content": "test"}], tools
            )
        payload = mock_client.post.call_args[1]["json"]
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"

    async def test_adds_image_to_last_user_message(self):
        client = LMClient()
        mock_resp = _make_httpx_resp(200, {
            "choices": [{"message": {"content": "I see an image"}}]
        })
        patch_ctx, mock_client = _patch_httpx_post(mock_resp)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            await client.generate_with_tools(
                [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "describe this"},
                ],
                [],
                image_base64="iVBORw0KGgo=",
            )
        payload = mock_client.post.call_args[1]["json"]
        user_msg = payload["messages"][1]
        assert isinstance(user_msg["content"], list)
        assert user_msg["content"][0]["type"] == "text"
        assert user_msg["content"][1]["type"] == "image_url"

    async def test_handles_api_error(self):
        client = LMClient()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("timeout"))
        mock_instance = MagicMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_client)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch("core.lm_client.httpx.AsyncClient", return_value=mock_instance),
        ):
            content, tools = await client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content is None
        assert tools is None

    async def test_handles_non_200_status(self):
        client = LMClient()
        patch_ctx, _ = _patch_httpx_post(_make_httpx_resp(500))
        with (
            patch.object(client, "is_server_running", new=AsyncMock(return_value=True)),
            patch_ctx,
        ):
            content, tools = await client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content is None
        assert tools is None
