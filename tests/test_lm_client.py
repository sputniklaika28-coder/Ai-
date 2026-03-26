"""
test_lm_client.py — LMClient のユニットテスト

テスト対象:
  - is_server_running()
  - _clean_response()
  - generate_response()

外部依存 requests はすべてモック化する。
"""

from unittest.mock import MagicMock, patch

from core.lm_client import LMClient

# ──────────────────────────────────────────
# is_server_running
# ──────────────────────────────────────────


class TestIsServerRunning:
    def test_returns_true_when_200(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("core.lm_client.requests.get", return_value=mock_resp):
            assert client.is_server_running() is True

    def test_returns_false_when_non_200(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("core.lm_client.requests.get", return_value=mock_resp):
            assert client.is_server_running() is False

    def test_returns_false_on_connection_error(self):
        client = LMClient()
        with patch("core.lm_client.requests.get", side_effect=ConnectionError):
            assert client.is_server_running() is False

    def test_hits_correct_endpoint(self):
        client = LMClient(base_url="http://localhost:9999")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("core.lm_client.requests.get", return_value=mock_resp) as mock_get:
            client.is_server_running()
            mock_get.assert_called_once_with("http://localhost:9999/v1/models", timeout=3)


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
        # 波括弧がなければ first_brace_idx == -1 → 元テキストをそのまま返す
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
    def _make_api_response(self, content: str):
        """OpenAI 互換レスポンスの dict を作る"""
        return {"choices": [{"message": {"content": content, "tool_calls": None}}]}

    def test_returns_none_when_server_down(self):
        client = LMClient()
        with patch.object(client, "is_server_running", return_value=False):
            content, tools = client.generate_response("sys", "user")
        assert content is None
        assert tools is None

    def test_returns_cleaned_json(self):
        client = LMClient()
        raw = '{"action": "cast"}'
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = self._make_api_response(raw)

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp),
        ):
            content, tools = client.generate_response("sys", "user")

        assert content == '{"action": "cast"}'
        assert tools is None

    def test_no_think_prepends_flag(self):
        client = LMClient()
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = self._make_api_response('{"ok": true}')

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp) as mock_post,
        ):
            client.generate_response("sys", "user", no_think=True)

        payload = mock_post.call_args[1]["json"]
        assert payload["messages"][0]["content"].startswith("/no_think")
        assert payload.get("chat_template_kwargs") == {"enable_thinking": False}

    def test_returns_none_on_non_200(self):
        client = LMClient()
        api_resp = MagicMock()
        api_resp.status_code = 503

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp),
        ):
            content, tools = client.generate_response("sys", "user")

        assert content is None

    def test_returns_none_on_exception(self):
        client = LMClient()
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", side_effect=TimeoutError),
        ):
            content, tools = client.generate_response("sys", "user")

        assert content is None

    def test_falls_back_to_reasoning_content_when_valid_json(self):
        """content が空で reasoning_content に有効な JSON が含まれる場合、フォールバックを採用する"""
        client = LMClient()
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": '思考中…\n{"action": "heal"}',
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp),
        ):
            content, tools = client.generate_response("sys", "user")

        assert content == '{"action": "heal"}'

    def test_no_fallback_when_reasoning_content_is_thinking_text(self):
        """reasoning_content が思考テキストのみで有効な JSON でない場合、空文字を返す（no_think=False でリトライなし）"""
        client = LMClient()
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": 'Thinking: Step 1 analyze {"partial": thinking...} more text',
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp),
        ):
            # no_think=False なのでリトライは発生しない
            content, tools = client.generate_response("sys", "user", no_think=False)

        assert content == ""

    def test_fallback_to_reasoning_when_finish_reason_length(self):
        """finish_reason=length でも reasoning_content 内に完結した JSON があれば抽出する"""
        client = LMClient()
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": 'Thinking: {"action": "heal", "target": "ally"} more thinking...',
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp),
        ):
            content, tools = client.generate_response("sys", "user")

        assert content == '{"action": "heal", "target": "ally"}'

    def test_retries_with_doubled_max_tokens_when_no_think_ignored(self):
        """no_think=True でモデルが思考を無視した場合、max_tokens を倍にしてリトライする"""
        client = LMClient()

        # 1回目: content 空 + reasoning_content に思考テキスト（JSON 検証不合格）
        first_resp = MagicMock()
        first_resp.status_code = 200
        first_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": "Thinking about the problem...",
                        "tool_calls": None,
                    },
                }
            ]
        }
        # 2回目: リトライ成功（倍の max_tokens で content が返る）
        retry_resp = MagicMock()
        retry_resp.status_code = 200
        retry_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"result": "success"}',
                        "reasoning_content": "Now I have enough tokens...",
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch(
                "core.lm_client.requests.post", side_effect=[first_resp, retry_resp]
            ) as mock_post,
        ):
            content, tools = client.generate_response("sys", "user", max_tokens=4096, no_think=True)

        assert content == '{"result": "success"}'
        # 2回呼ばれたことを確認
        assert mock_post.call_count == 2
        # 2回目の max_tokens が倍になっていることを確認
        retry_payload = mock_post.call_args_list[1][1]["json"]
        assert retry_payload["max_tokens"] == 8192

    def test_no_retry_when_no_think_is_false_and_finish_reason_stop(self):
        """no_think=False かつ finish_reason=stop の場合、思考が無視されてもリトライしない"""
        client = LMClient()
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": "Thinking...",
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp) as mock_post,
        ):
            content, tools = client.generate_response("sys", "user", no_think=False)

        assert content == ""
        assert mock_post.call_count == 1  # リトライなし

    def test_retries_on_finish_reason_length_without_no_think(self):
        """no_think=False でも finish_reason=length で content 空なら max_tokens×2 でリトライする"""
        client = LMClient()

        # 1回目: finish_reason=length, content 空
        first_resp = MagicMock()
        first_resp.status_code = 200
        first_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": "長い思考テキスト...",
                        "tool_calls": None,
                    },
                }
            ]
        }
        # 2回目: リトライ成功
        retry_resp = MagicMock()
        retry_resp.status_code = 200
        retry_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"name": "リトライ成功"}',
                        "reasoning_content": "",
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch(
                "core.lm_client.requests.post", side_effect=[first_resp, retry_resp]
            ) as mock_post,
        ):
            content, tools = client.generate_response(
                "sys", "user", max_tokens=8192, no_think=False
            )

        assert content == '{"name": "リトライ成功"}'
        assert mock_post.call_count == 2
        retry_payload = mock_post.call_args_list[1][1]["json"]
        assert retry_payload["max_tokens"] == 16384

    def test_final_retry_on_finish_reason_length_without_no_think(self):
        """no_think=False でも finish_reason=length が連続すれば最終リトライ（×4）する"""
        client = LMClient()

        empty_resp = MagicMock()
        empty_resp.status_code = 200
        empty_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": "思考のみ...",
                        "tool_calls": None,
                    },
                }
            ]
        }

        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"name": "最終リトライ成功"}',
                        "reasoning_content": "",
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch(
                "core.lm_client.requests.post",
                side_effect=[empty_resp, empty_resp, final_resp],
            ) as mock_post,
        ):
            content, _ = client.generate_response(
                "sys", "user", max_tokens=8192, no_think=False, temperature=0.7
            )

        assert mock_post.call_count == 3
        assert "最終リトライ成功" in content
        final_payload = mock_post.call_args_list[2][1]["json"]
        assert final_payload["max_tokens"] == 8192 * 4
        assert abs(final_payload["temperature"] - 0.8) < 1e-9

    def test_empty_tool_calls_list_normalized_to_none(self):
        """tool_calls が空リスト [] の場合、None に正規化する"""
        client = LMClient()
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"action": "wait"}',
                        "tool_calls": [],
                    }
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp),
        ):
            content, tools = client.generate_response("sys", "user")

        assert tools is None

    def test_custom_base_url_and_model(self):
        client = LMClient(base_url="http://myserver:5678", model="my-model")
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = self._make_api_response('{"x": 1}')

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp) as mock_post,
        ):
            client.generate_response("sys", "user")

        url = mock_post.call_args[0][0]
        assert "myserver:5678" in url
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "my-model"

    def test_extracts_json_from_reasoning_with_thinking_text(self):
        """reasoning_content に思考テキストとJSONが混在する場合、JSONを抽出する"""
        client = LMClient()
        embedded_json = '{"name": "テスト太郎", "body": 4, "soul": 3}'
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": f"まずキャラを考えます…\n{embedded_json}\nこれで完成です。",
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=api_resp),
        ):
            content, _ = client.generate_response("sys", "user", no_think=False)

        assert '"name"' in content
        assert "テスト太郎" in content

    def test_final_retry_removes_thinking_and_increases_tokens(self):
        """2回リトライしても空の場合、思考制限を完全解除して最終リトライする"""
        client = LMClient()
        empty_resp = MagicMock()
        empty_resp.status_code = 200
        empty_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": "ただの思考テキスト",
                        "tool_calls": None,
                    },
                }
            ]
        }

        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": 'キャラを作ります。{"name": "最終太郎", "hp": 4}。以上。',
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch(
                "core.lm_client.requests.post",
                side_effect=[empty_resp, empty_resp, final_resp],
            ) as mock_post,
        ):
            content, _ = client.generate_response(
                "sys", "user", no_think=True, max_tokens=4096, temperature=0.1
            )

        assert mock_post.call_count == 3
        final_payload = mock_post.call_args_list[2][1]["json"]
        # chat_template_kwargs が除去されている
        assert "chat_template_kwargs" not in final_payload
        # /no_think プレフィックスが除去されている
        assert not final_payload["messages"][0]["content"].startswith("/no_think")
        # max_tokens が ×4 に拡大されている
        assert final_payload["max_tokens"] == 4096 * 4
        # temperature が +0.1 されている
        assert final_payload["temperature"] == 0.2
        assert "最終太郎" in content

    def test_final_retry_caps_temperature_at_1(self):
        """最終リトライの temperature は 1.0 を超えない"""
        client = LMClient()
        empty_resp = MagicMock()
        empty_resp.status_code = 200
        empty_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "",
                        "reasoning_content": "思考のみ",
                        "tool_calls": None,
                    },
                }
            ]
        }

        final_resp = MagicMock()
        final_resp.status_code = 200
        final_resp.json.return_value = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"ok": true}',
                        "reasoning_content": "",
                        "tool_calls": None,
                    },
                }
            ]
        }

        with (
            patch.object(client, "is_server_running", return_value=True),
            patch(
                "core.lm_client.requests.post",
                side_effect=[empty_resp, empty_resp, final_resp],
            ) as mock_post,
        ):
            client.generate_response("sys", "user", no_think=True, max_tokens=300, temperature=0.95)

        final_payload = mock_post.call_args_list[2][1]["json"]
        assert final_payload["temperature"] == 1.0


# ──────────────────────────────────────────
# generate_with_tools
# ──────────────────────────────────────────


class TestGenerateWithTools:
    def test_returns_none_when_server_not_running(self):
        client = LMClient()
        with patch.object(client, "is_server_running", return_value=False):
            content, tools = client.generate_with_tools(
                [{"role": "user", "content": "hello"}],
                [],
            )
        assert content is None
        assert tools is None

    def test_returns_content_and_tool_calls(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
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
                }
            ]
        }
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=mock_resp),
        ):
            content, tool_calls = client.generate_with_tools(
                [{"role": "user", "content": "test"}],
                [{"type": "function", "function": {"name": "post_chat"}}],
            )
        assert content == "考えています"
        assert tool_calls is not None
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "post_chat"

    def test_returns_content_only_when_no_tool_calls(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "plain text response"}}]
        }
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=mock_resp),
        ):
            content, tool_calls = client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content == "plain text response"
        assert tool_calls is None

    def test_strips_think_tags_from_content(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {"message": {"content": "<think>考え中</think>結果のテキスト"}}
            ]
        }
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=mock_resp),
        ):
            content, _ = client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content == "結果のテキスト"

    def test_includes_tools_in_payload(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=mock_resp) as mock_post,
        ):
            client.generate_with_tools(
                [{"role": "user", "content": "test"}], tools
            )
        payload = mock_post.call_args[1]["json"]
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"

    def test_adds_image_to_last_user_message(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "I see an image"}}]
        }
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=mock_resp) as mock_post,
        ):
            client.generate_with_tools(
                [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "describe this"},
                ],
                [],
                image_base64="iVBORw0KGgo=",
            )
        payload = mock_post.call_args[1]["json"]
        user_msg = payload["messages"][1]
        assert isinstance(user_msg["content"], list)
        assert user_msg["content"][0]["type"] == "text"
        assert user_msg["content"][1]["type"] == "image_url"

    def test_handles_api_error(self):
        client = LMClient()
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", side_effect=Exception("timeout")),
        ):
            content, tools = client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content is None
        assert tools is None

    def test_handles_non_200_status(self):
        client = LMClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with (
            patch.object(client, "is_server_running", return_value=True),
            patch("core.lm_client.requests.post", return_value=mock_resp),
        ):
            content, tools = client.generate_with_tools(
                [{"role": "user", "content": "test"}], []
            )
        assert content is None
        assert tools is None
