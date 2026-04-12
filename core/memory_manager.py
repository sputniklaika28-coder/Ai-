"""memory_manager.py — ローリング要約による会話履歴の軽量化管理。

会話履歴が SUMMARY_THRESHOLD を超えたとき、古い履歴をバックグラウンドの
軽量 LLM で要約圧縮する。短期記憶（直近 N 件の生テキスト）と
長期記憶（要約テキスト）を分離して保持し、コンテキストウィンドウを
常に最適なサイズに保つ。

ai_compressor..py のロジックをコアエンジンとして統合・昇格させたもの。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MemoryStats:
    """MemoryManager の統計情報。"""

    total_messages: int = 0
    summary_count: int = 0          # 要約を実施した回数
    current_recent_count: int = 0   # 現在の短期記憶件数
    has_summary: bool = False        # 長期要約が存在するか


class MemoryManager:
    """ローリング要約による会話履歴管理。

    動作フロー:
    1. `add_message()` でメッセージを短期記憶 (_recent) に追加
    2. 件数が `summary_threshold` を超えると、古いメッセージを LLM で要約
    3. 要約は `_summary` に蓄積し、直近 `recent_keep` 件は生テキストを保持
    4. `get_context_window()` は [あらすじ] + [直近の会話] を返す

    LMClient が利用できない場合は単純な切り詰めにフォールバックする。
    """

    # ──────────────────────────────────────────
    # デフォルト設定
    # ──────────────────────────────────────────
    DEFAULT_SUMMARY_THRESHOLD = 40   # これを超えたら要約トリガー
    DEFAULT_RECENT_KEEP = 15         # 要約後に生で残す直近件数
    SUMMARY_MAX_TOKENS = 800         # 要約の最大トークン数
    SUMMARY_TEMPERATURE = 0.2        # 低温で正確に要約

    _SUMMARY_SYSTEM_PROMPT = (
        "あなたはTRPGセッションの記録係です。\n"
        "入力されたセッションログから以下の要素を保持しつつ、\n"
        "無駄な繰り返しや装飾を省いて箇条書きで簡潔に要約してください:\n"
        "- 重要な出来事・戦闘結果\n"
        "- NPCの言動・明らかになった情報\n"
        "- 現在のHP/MP等のステータス変化\n"
        "- 未解決のフラグ・伏線\n\n"
        "出力は要約テキストのみとし、挨拶や解説は含めないでください。"
    )

    def __init__(
        self,
        lm_client=None,
        summary_threshold: int = DEFAULT_SUMMARY_THRESHOLD,
        recent_keep: int = DEFAULT_RECENT_KEEP,
    ) -> None:
        """MemoryManager を初期化する。

        Args:
            lm_client: LMClient インスタンス。None の場合は要約なし（切り詰めのみ）。
            summary_threshold: 要約をトリガーするメッセージ数の閾値。
            recent_keep: 要約後に生で残す直近メッセージ件数。
        """
        self.lm_client = lm_client
        self.summary_threshold = summary_threshold
        self.recent_keep = recent_keep

        self._recent: list[dict] = []     # 短期記憶（生テキスト）
        self._summary: str = ""            # 長期記憶（要約テキスト）
        self._total_messages: int = 0
        self._summary_count: int = 0
        self._summarizing: bool = False    # 重複要約防止フラグ
        self._lock = threading.Lock()

    # ──────────────────────────────────────────
    # メッセージ追加
    # ──────────────────────────────────────────

    def add_message(self, speaker: str, body: str) -> None:
        """メッセージを短期記憶に追加し、必要なら要約をトリガーする。

        Args:
            speaker: 発言者名。
            body: 発言内容。
        """
        with self._lock:
            self._recent.append({"speaker": speaker, "body": body})
            self._total_messages += 1

            # 閾値超過 かつ 要約中でなければバックグラウンドで要約
            if (
                len(self._recent) >= self.summary_threshold
                and not self._summarizing
            ):
                self._summarizing = True
                t = threading.Thread(target=self._compress_history, daemon=False)
                t.start()

    # ──────────────────────────────────────────
    # コンテキストウィンドウ取得
    # ──────────────────────────────────────────

    def get_context_window(self) -> str:
        """LLM に渡す最適化されたコンテキスト文字列を返す。

        Returns:
            [あらすじ] + [直近の会話] の形式の文字列。
        """
        with self._lock:
            parts: list[str] = []
            if self._summary:
                parts.append(f"【これまでのあらすじ】\n{self._summary}")
            if self._recent:
                lines = [f"[{m['speaker']}]: {m['body']}" for m in self._recent]
                parts.append("【直近の会話】\n" + "\n".join(lines))
            return "\n\n".join(parts)

    def get_recent_messages(self) -> list[dict]:
        """短期記憶のコピーを返す。"""
        with self._lock:
            return list(self._recent)

    def get_summary(self) -> str:
        """現在の長期要約テキストを返す。"""
        with self._lock:
            return self._summary

    def get_stats(self) -> MemoryStats:
        with self._lock:
            return MemoryStats(
                total_messages=self._total_messages,
                summary_count=self._summary_count,
                current_recent_count=len(self._recent),
                has_summary=bool(self._summary),
            )

    # ──────────────────────────────────────────
    # 履歴圧縮（バックグラウンド）
    # ──────────────────────────────────────────

    def _compress_history(self) -> None:
        """古いメッセージを LLM で要約してメモリを解放する。"""
        try:
            with self._lock:
                if len(self._recent) < self.summary_threshold:
                    return
                # スナップショットのみ。_recent はまだ触らない
                to_compress = self._recent[: -self.recent_keep]

            if not to_compress:
                return

            # 既存の要約 + 圧縮対象テキストを合わせて新要約を生成
            raw_text = "\n".join(
                f"[{m['speaker']}]: {m['body']}" for m in to_compress
            )
            full_text = (
                f"【前回までのあらすじ】\n{self._summary}\n\n【続き】\n{raw_text}"
                if self._summary else raw_text
            )

            new_summary = self._call_summary_llm(full_text)

            if not new_summary:
                # 要約失敗 → _recent 保持、次回再試行
                logger.warning("要約失敗 — 元のメッセージを保持します。次の閾値到達時に再試行します。")
                return

            # 要約成功 → ここで初めて _recent を削減
            with self._lock:
                # LLM呼び出し中に追加されたメッセージも保全するため、先頭からN件削除
                self._recent = self._recent[len(to_compress):]
                self._summary = new_summary
                self._summary_count += 1
                logger.info(
                    "履歴要約完了: %d件 → %d文字 (計%d回目)",
                    len(to_compress),
                    len(new_summary),
                    self._summary_count,
                )
        except Exception as e:
            logger.error("履歴圧縮エラー: %s", e)
        finally:
            self._summarizing = False

    def _call_summary_llm(self, text: str) -> str:
        """LLM でテキストを要約する。失敗時は空文字を返す（呼び出し元でハンドリング）。"""
        if self.lm_client is None:
            return ""  # LLM なし → 要約しない（_recent を保持）

        try:
            result, _ = self.lm_client.generate_response_sync(
                system_prompt=self._SUMMARY_SYSTEM_PROMPT,
                user_message=f"以下のセッションログを要約してください:\n\n{text}",
                temperature=self.SUMMARY_TEMPERATURE,
                max_tokens=self.SUMMARY_MAX_TOKENS,
            )
            return result.strip() if result else ""
        except Exception as e:
            logger.warning("LLM要約エラー: %s — 次回再試行します", e)
            return ""

    @staticmethod
    def _fallback_truncate(text: str, max_lines: int = 20) -> str:
        """LLM 不使用時の単純切り詰めフォールバック。"""
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) <= max_lines:
            return text
        return "\n".join(lines[:max_lines]) + f"\n... (以下{len(lines) - max_lines}行省略)"

    # ──────────────────────────────────────────
    # 後方互換 API（SessionContext 置き換え用）
    # ──────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        """後方互換: SessionContext.history 相当のアクセスを提供する。"""
        return self.get_recent_messages()

    def get_context_summary(self) -> str:
        """後方互換: SessionContext.get_context_summary() 相当。"""
        return self.get_context_window()
