from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from typing import TYPE_CHECKING, Type, TypeVar

import httpx

if TYPE_CHECKING:
    from pydantic import BaseModel as _BaseModel

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class LMClient:
    def __init__(self, base_url: str = "http://localhost:1234", model: str = "local-model"):
        self.base_url = base_url
        self.model = model

    async def is_server_running(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self.base_url}/v1/models")
            return response.status_code == 200
        except Exception:
            return False

    def _strip_think_tags(self, text: str) -> str:
        """<think>...</think> タグを除去して後続テキストを返す。"""
        if "</think>" in text:
            text = text.split("</think>")[-1].strip()
        return text

    def _clean_response(self, text: str) -> str:
        """AIの余計な独り言や思考プロセスを完全に削ぎ落とし、純粋なJSONだけを抽出する"""

        # 1. もし <think> タグが含まれていたら、その後ろだけを切り出す
        text = self._strip_think_tags(text)

        # 2. 「思考プロセス：」などの日本語の独り言が含まれていた場合、
        #    最初の `{` が出現するまでの文字をすべてゴミとして切り捨てる
        first_brace_idx = text.find("{")
        if first_brace_idx != -1:
            # 最初の { から後ろを切り出す
            text = text[first_brace_idx:]

        # 3. 最後の `}` より後ろにあるゴミ（「出力完了しました」など）を切り捨てる
        last_brace_idx = text.rfind("}")
        if last_brace_idx != -1:
            # 最初の { から最後の } までを正確に抜き出す
            text = text[: last_brace_idx + 1]

        # 4. マークダウン（```json 〜 ```）が残っていたら綺麗に剥がす
        cb = chr(96) * 3
        pattern = cb + r"(?:json)?\s*(\{.*?\})\s*" + cb
        match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1)

        return text.strip()

    def _find_json_in_text(self, text: str) -> str:
        """テキスト内から最大の有効な JSON オブジェクトを探して返す。
        見つからなければ空文字を返す。"""
        # すべての { の位置を探索し、対応する } までが有効な JSON かを試す
        best = ""
        i = 0
        while i < len(text):
            start = text.find("{", i)
            if start == -1:
                break
            # 末尾から逆順で } を探し、最大の有効 JSON を優先
            depth = 0
            for j in range(start, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : j + 1]
                        try:
                            json.loads(candidate)
                            if len(candidate) > len(best):
                                best = candidate
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break
            i = start + 1
        return best

    def _extract_content(self, result: dict) -> tuple[str, bool, str, bool]:
        """API レスポンスから content を抽出する。

        Returns:
            (raw_content, thinking_ignored, finish_reason, content_was_empty):
            - raw_content: モデルの出力テキスト（reasoning フォールバック適用後）
            - thinking_ignored: content が空で reasoning_content に思考が入っていた場合 True
            - finish_reason: API レスポンスの finish_reason
            - content_was_empty: 元の content が空だった場合 True（reasoning フォールバック前）
        """
        message = result["choices"][0]["message"]
        raw_content = message.get("content") or ""
        finish_reason = result["choices"][0].get("finish_reason", "")
        reasoning = (message.get("reasoning_content") or "").strip()
        has_reasoning = bool(reasoning)

        print(
            f"DEBUG: content長={len(raw_content.strip())}, "
            f"reasoning長={len(reasoning)}, "
            f"finish_reason={finish_reason}"
        )

        content_was_empty = not raw_content.strip()

        if content_was_empty and has_reasoning:
            # reasoning_content 内から有効な JSON を探す（思考テキスト混在対応）
            # finish_reason が length（トークン上限）でも、途中に完結した JSON があれば抽出する
            found_json = self._find_json_in_text(reasoning)
            if found_json:
                print(f"DEBUG: reasoning_content内からJSON抽出成功 (長さ={len(found_json)})")
                raw_content = found_json

        thinking_ignored = not raw_content.strip() and has_reasoning
        return raw_content, thinking_ignored, finish_reason, content_was_empty

    async def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.75,
        max_tokens: int = 300,
        timeout: int | None = 600,
        top_p: float = 0.9,
        top_k: int = 20,
        presence_penalty: float = 0.0,
        repetition_penalty: float = 1.0,
        min_p: float = 0.0,
        no_think: bool = False,
        json_mode: bool = False,
    ):
        if not await self.is_server_running():
            return None, None

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        payload_messages = copy.deepcopy(messages)

        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "top_k": top_k,
            "min_p": min_p,
            "presence_penalty": presence_penalty,
            "repetition_penalty": repetition_penalty,
            "stream": False,
        }
        if no_think:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions", json=payload
                )
            if response.status_code == 200:
                result = response.json()
                raw_content, _, finish_reason, content_was_empty = (
                    self._extract_content(result)
                )
                active_result = result

                # reasoning_content から有効な JSON を抽出済みならリトライ不要
                json_recovered = content_was_empty and bool(raw_content.strip())

                # リトライ判定: finish_reason=length で content が空の場合のみ
                needs_retry = not json_recovered and (
                    finish_reason == "length" and content_was_empty
                )

                if needs_retry:
                    print("DEBUG: content空(length) → max_tokens×2でリトライ")
                    retry_payload = {
                        **payload,
                        "max_tokens": max_tokens * 2,
                    }
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        retry_resp = await client.post(
                            f"{self.base_url}/v1/chat/completions",
                            json=retry_payload,
                        )
                    if retry_resp.status_code == 200:
                        retry_result = retry_resp.json()
                        active_result = retry_result
                        raw_content, _, _, _ = self._extract_content(retry_result)

                content = self._clean_response(raw_content)
                tool_calls = active_result["choices"][0]["message"].get("tool_calls") or None

                return content, tool_calls
            return None, None
        except Exception as e:
            print(f"   ⚠️  LM-Studio通信エラー: {str(e)}")
            return None, None

    def generate_response_sync(self, *args, **kwargs):
        """threading 環境からの呼び出し用同期ラッパー。"""
        return asyncio.run(self.generate_response(*args, **kwargs))

    async def generate_structured(
        self,
        system_prompt: str,
        user_message: str,
        schema: "Type[_T]",
        *,
        temperature: float = 0.3,
        max_tokens: int = 500,
        timeout: int | None = 120,
        strict: bool = True,
    ) -> "_T | None":
        """Pydantic スキーマを用いた構造化出力生成。

        OpenAI json_schema モードでモデルレベルの JSON 整合性を強制する。
        正規表現・ブルートフォース探索は一切使用しない。

        Args:
            system_prompt: システムプロンプト。
            user_message: ユーザーメッセージ。
            schema: 期待する Pydantic v2 モデルクラス。
            temperature: 生成温度（構造化出力は低めを推奨）。
            max_tokens: 最大トークン数。
            timeout: タイムアウト（秒）。
            strict: json_schema の strict モード（True推奨）。

        Returns:
            バリデーション済みの Pydantic モデルインスタンス。
            失敗した場合は None。
        """
        from pydantic import BaseModel, ValidationError

        if not await self.is_server_running():
            return None

        json_schema = schema.model_json_schema()  # type: ignore[attr-defined]

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": json_schema,
                    "strict": strict,
                },
            },
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions", json=payload
                )

            if response.status_code != 200:
                logger.warning(
                    "generate_structured: HTTP %d — %s",
                    response.status_code,
                    response.text[:200],
                )
                return None

            result = response.json()
            message = result["choices"][0]["message"]
            raw_content = message.get("content") or ""

            # 推論モデル: content が空の場合 reasoning_content から JSON を探す
            if not raw_content.strip():
                reasoning = (message.get("reasoning_content") or "").strip()
                if reasoning:
                    found = self._find_json_in_text(reasoning)
                    if found:
                        raw_content = found

            if not raw_content.strip():
                logger.warning("generate_structured: empty content for %s", schema.__name__)
                return None

            return schema.model_validate_json(raw_content)  # type: ignore[attr-defined]

        except ValidationError as e:
            logger.warning("generate_structured: ValidationError for %s: %s", schema.__name__, e)
            return None
        except Exception as e:
            logger.error("generate_structured エラー (%s): %s", schema.__name__, e)
            return None

    def generate_structured_sync(self, *args, **kwargs) -> "_T | None":
        """threading 環境からの呼び出し用同期ラッパー。"""
        return asyncio.run(self.generate_structured(*args, **kwargs))

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 1500,
        timeout: int | None = 600,
        image_base64: str | None = None,
    ) -> tuple[str | None, list[dict] | None]:
        """ツール呼び出し対応のLLM推論。マルチターンメッセージ対応。

        Args:
            messages: OpenAI形式のメッセージリスト（system/user/assistant/tool）。
            tools: ツール定義のリスト（OpenAI function calling形式）。
            temperature: 生成温度。
            max_tokens: 最大トークン数。
            timeout: リクエストタイムアウト（秒）。
            image_base64: ビジョンモデル用のBase64エンコード画像（省略可）。

        Returns:
            (content, tool_calls) のタプル。
            - content: モデルの出力テキスト（tool_callsがある場合は思考テキスト）。
            - tool_calls: ツール呼び出しリスト。呼び出しがない場合はNone。
        """
        if not await self.is_server_running():
            return None, None

        # メッセージを深コピーして画像を追加
        payload_messages = copy.deepcopy(messages)

        # 画像がある場合、最後の user メッセージにマルチモーダルコンテンツとして追加
        if image_base64:
            for msg in reversed(payload_messages):
                if msg["role"] == "user":
                    text_content = msg.get("content", "")
                    if isinstance(text_content, str):
                        msg["content"] = [
                            {"type": "text", "text": text_content},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_base64}",
                                },
                            },
                        ]
                    break

        payload: dict = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        # ツール定義を追加（空の場合はツール無し推論）
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                )
            if response.status_code == 200:
                result = response.json()
                message = result["choices"][0]["message"]
                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or None

                # <think> タグがある場合はクリーンアップ
                content = self._strip_think_tags(content)

                return content or None, tool_calls
            return None, None
        except Exception as e:
            print(f"   ⚠️  LM-Studio通信エラー (generate_with_tools): {str(e)}")
            return None, None
