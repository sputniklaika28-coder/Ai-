from __future__ import annotations

import copy
import json
import re

import requests


class LMClient:
    def __init__(self, base_url: str = "http://localhost:1234", model: str = "local-model"):
        self.base_url = base_url
        self.model = model

    def is_server_running(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/v1/models", timeout=3)
            return response.status_code == 200
        except Exception:
            return False

    def _clean_response(self, text: str) -> str:
        """AIの余計な独り言や思考プロセスを完全に削ぎ落とし、純粋なJSONだけを抽出する"""

        # 1. もし <think> タグが含まれていたら、その後ろだけを切り出す
        if "</think>" in text:
            text = text.split("</think>")[-1]

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

    def generate_response(
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
    ):
        if not self.is_server_running():
            return None, None

        # Qwen3系などの思考モデルで思考を抑制する
        # chat_template_kwargs でllama.cpp/LM Studioに指示し、
        # システムプロンプト先頭の /no_think でモデルにも直接指示する
        effective_system = system_prompt
        if no_think:
            effective_system = "/no_think\n" + system_prompt

        messages = [
            {"role": "system", "content": effective_system},
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

        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions", json=payload, timeout=timeout
            )
            if response.status_code == 200:
                result = response.json()
                raw_content, thinking_ignored, finish_reason, content_was_empty = (
                    self._extract_content(result)
                )

                # リトライ判定:
                # 1. no_think が無視されて content が空（思考トークンで消費しきった）
                # 2. finish_reason=length で元の content が空（トークン上限到達）
                needs_retry = (thinking_ignored and no_think) or (
                    finish_reason == "length" and content_was_empty
                )

                if needs_retry:
                    print("DEBUG: content空検出 → max_tokens×2でリトライ")
                    retry_payload = {**payload, "max_tokens": max_tokens * 2}
                    retry_resp = requests.post(
                        f"{self.base_url}/v1/chat/completions",
                        json=retry_payload,
                        timeout=timeout,
                    )
                    if retry_resp.status_code == 200:
                        retry_result = retry_resp.json()
                        raw_content, still_ignored, retry_finish, retry_empty = (
                            self._extract_content(retry_result)
                        )

                        # それでもダメなら最終リトライ
                        # ・max_tokens を ×4 に拡大（思考+出力の両方に十分な余裕）
                        # ・temperature を微増して決定論的な失敗ループを回避
                        # ・no_think の場合は思考制限を完全に解除
                        still_needs_retry = (still_ignored and no_think) or (
                            retry_finish == "length" and retry_empty
                        )
                        if still_needs_retry:
                            print("DEBUG: リトライも空 → max_tokens×4で最終リトライ")
                            final_messages = [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_message},
                            ]
                            final_payload = {
                                k: v for k, v in payload.items() if k != "chat_template_kwargs"
                            }
                            final_payload["messages"] = final_messages
                            final_payload["max_tokens"] = max_tokens * 4
                            final_payload["temperature"] = min(temperature + 0.1, 1.0)
                            final_resp = requests.post(
                                f"{self.base_url}/v1/chat/completions",
                                json=final_payload,
                                timeout=timeout,
                            )
                            if final_resp.status_code == 200:
                                final_result = final_resp.json()
                                raw_content, _, _, _ = self._extract_content(final_result)

                # ログを見ると、AIがJSONの中にさらに思考を書き込んでいる場合があるため、クリーン処理にかける
                content = self._clean_response(raw_content)
                tool_calls = result["choices"][0]["message"].get("tool_calls") or None

                return content, tool_calls
            return None, None
        except Exception as e:
            print(f"   ⚠️  LM-Studio通信エラー: {str(e)}")
            return None, None

    def generate_with_tools(
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
        if not self.is_server_running():
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
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=timeout,
            )
            if response.status_code == 200:
                result = response.json()
                message = result["choices"][0]["message"]
                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or None

                # <think> タグがある場合はクリーンアップ
                if content and "</think>" in content:
                    content = content.split("</think>")[-1].strip()

                return content or None, tool_calls
            return None, None
        except Exception as e:
            print(f"   ⚠️  LM-Studio通信エラー (generate_with_tools): {str(e)}")
            return None, None
