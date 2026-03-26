"""knowledge_manager.py — RAG（ベクトル検索）+ Web検索 統合マネージャー。

ChromaDB を使ったローカルベクトルDB検索と、
DuckDuckGo Search を使ったウェブ検索を統合的に提供する。

ルールブック、世界観設定、セッションログなどを
チャンク分割してベクトルDBに登録し、LLMが検索ツールとして利用する。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# テキスト分割のデフォルト設定
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50


class KnowledgeManager:
    """RAG ベクトル検索 + ウェブ検索を提供するナレッジマネージャー。

    Attributes:
        client: ChromaDB の永続化クライアント。
        collection: ドキュメントを格納する ChromaDB コレクション。
    """

    def __init__(
        self,
        persist_dir: str = "data/chroma_db",
        collection_name: str = "tactical_ai_knowledge",
    ) -> None:
        """KnowledgeManager を初期化する。

        Args:
            persist_dir: ChromaDB の永続化ディレクトリ。
            collection_name: 使用するコレクション名。
        """
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.client = None
        self.collection = None

        try:
            import chromadb
            from chromadb.config import Settings

            self.client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "KnowledgeManager 初期化: persist_dir=%s, collection=%s (docs=%d)",
                self.persist_dir,
                collection_name,
                self.collection.count(),
            )
        except ImportError:
            logger.warning(
                "chromadb が未インストールです。ベクトル検索機能は無効化されます。"
                " pip install chromadb でインストールしてください。"
            )

    # ──────────────────────────────────────────
    # ドキュメント登録
    # ──────────────────────────────────────────

    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict] | None = None,
        source: str = "unknown",
    ) -> int:
        """テキストをベクトルDBに登録する。

        Args:
            texts: 登録するテキストチャンクのリスト。
            metadatas: 各テキストに紐づくメタデータ（省略時は source のみ）。
            source: メタデータのデフォルト source 値。

        Returns:
            登録されたドキュメント数。
        """
        if not texts:
            return 0

        if self.collection is None:
            logger.warning("ChromaDB 未初期化のためドキュメント登録をスキップしました。")
            return 0

        if metadatas is None:
            metadatas = [{"source": source}] * len(texts)

        # ChromaDB 用の一意IDを生成
        existing_count = self.collection.count()
        ids = [f"doc_{existing_count + i}" for i in range(len(texts))]

        self.collection.add(documents=texts, metadatas=metadatas, ids=ids)
        logger.info("%d 件のドキュメントを登録しました (source=%s)", len(texts), source)
        return len(texts)

    # ──────────────────────────────────────────
    # ベクトル検索
    # ──────────────────────────────────────────

    def search_knowledge_base(self, query: str, n_results: int = 5) -> list[dict]:
        """ベクトル類似検索でドキュメントを検索する。

        Args:
            query: 検索クエリテキスト。
            n_results: 返す結果の最大数。

        Returns:
            検索結果のリスト。各要素は以下のキーを含む:
            - text: str — マッチしたテキスト
            - metadata: dict — メタデータ
            - distance: float — コサイン距離（小さいほど類似）
        """
        if self.collection is None:
            logger.warning("ChromaDB 未初期化のため検索をスキップしました。")
            return []

        if self.collection.count() == 0:
            logger.warning("コレクションが空です。先にドキュメントを登録してください。")
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, self.collection.count()),
        )

        output: list[dict] = []
        if results and results["documents"]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)
            dists = results["distances"][0] if results["distances"] else [0.0] * len(docs)
            for doc, meta, dist in zip(docs, metas, dists, strict=False):
                output.append({"text": doc, "metadata": meta, "distance": dist})

        return output

    # ──────────────────────────────────────────
    # ウェブ検索
    # ──────────────────────────────────────────

    def search_web(self, query: str, max_results: int = 3) -> list[dict]:
        """DuckDuckGo でウェブ検索を行う。

        Args:
            query: 検索クエリ。
            max_results: 返す結果の最大数。

        Returns:
            検索結果のリスト。各要素は以下のキーを含む:
            - title: str — ページタイトル
            - url: str — ページURL
            - snippet: str — 要約テキスト
        """
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.warning(
                "duckduckgo-search が未インストールです。ウェブ検索は利用できません。"
                " pip install duckduckgo-search でインストールしてください。"
            )
            return []

        try:
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(query, max_results=max_results))

            output: list[dict] = []
            for r in raw_results:
                output.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
            return output

        except Exception as e:
            logger.error("ウェブ検索エラー: %s", e)
            return []

    # ──────────────────────────────────────────
    # データ取り込み
    # ──────────────────────────────────────────

    def ingest_world_setting(self, path: str | Path) -> int:
        """world_setting.json をチャンク分割してベクトルDBに登録する。

        Args:
            path: world_setting.json のパス。

        Returns:
            登録されたチャンク数。
        """
        path = Path(path)
        if not path.exists():
            logger.warning("ファイルが見つかりません: %s", path)
            return 0

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            # world_setting.json は {key: value_text, ...} の形式
            full_text = "\n".join(v for v in data.values() if isinstance(v, str) and v)
            chunks = self._split_text(full_text)

            return self.add_documents(
                texts=chunks,
                metadatas=[{"source": "world_setting", "chunk_index": i} for i in range(len(chunks))],
                source="world_setting",
            )
        except Exception as e:
            logger.error("世界観データ取り込みエラー: %s", e)
            return 0

    def ingest_session_log(self, path: str | Path) -> int:
        """JSONL セッションログをベクトルDBに登録する。

        Args:
            path: chat_log.jsonl のパス。

        Returns:
            登録されたチャンク数。
        """
        path = Path(path)
        if not path.exists():
            logger.warning("ファイルが見つかりません: %s", path)
            return 0

        try:
            entries: list[str] = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        speaker = entry.get("speaker", "?")
                        body = entry.get("body", "")
                        entries.append(f"[{speaker}]: {body}")
                    except json.JSONDecodeError:
                        continue

            if not entries:
                return 0

            # セッションログは会話の流れが重要なので、
            # 複数エントリをまとめてチャンク化する
            full_text = "\n".join(entries)
            chunks = self._split_text(full_text, chunk_size=800, overlap=100)

            return self.add_documents(
                texts=chunks,
                metadatas=[
                    {"source": "session_log", "file": str(path.name), "chunk_index": i}
                    for i in range(len(chunks))
                ],
                source="session_log",
            )
        except Exception as e:
            logger.error("セッションログ取り込みエラー: %s", e)
            return 0

    # ──────────────────────────────────────────
    # ユーティリティ
    # ──────────────────────────────────────────

    @staticmethod
    def _split_text(
        text: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> list[str]:
        """テキストをチャンクに分割する。

        段落区切り（空行）を優先し、それが無い場合は
        文末（。！？）で分割する。

        Args:
            text: 分割するテキスト。
            chunk_size: 各チャンクの最大文字数。
            overlap: チャンク間の重複文字数。

        Returns:
            テキストチャンクのリスト。
        """
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        # 段落区切り（空行、見出し記号）で分割を試みる
        paragraphs = re.split(r"\n{2,}|(?=【)", text)
        chunks: list[str] = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 1 <= chunk_size:
                current = f"{current}\n{para}" if current else para
            else:
                if current:
                    chunks.append(current.strip())
                # 段落自体が chunk_size を超える場合は文末で分割
                if len(para) > chunk_size:
                    sub_chunks = KnowledgeManager._split_by_sentence(para, chunk_size, overlap)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    # オーバーラップ: 前チャンクの末尾を次チャンクの先頭に含める
                    if chunks and overlap > 0:
                        tail = chunks[-1][-overlap:]
                        current = f"{tail}\n{para}"
                    else:
                        current = para

        if current.strip():
            chunks.append(current.strip())

        return chunks

    @staticmethod
    def _split_by_sentence(
        text: str, chunk_size: int, overlap: int
    ) -> list[str]:
        """文末（。！？）で分割してチャンク化する。"""
        sentences = re.split(r"(?<=[。！？\n])", text)
        chunks: list[str] = []
        current = ""

        for sentence in sentences:
            if not sentence:
                continue
            if len(current) + len(sentence) <= chunk_size:
                current += sentence
            else:
                if current:
                    chunks.append(current.strip())
                if overlap > 0 and current:
                    current = current[-overlap:] + sentence
                else:
                    current = sentence

        if current.strip():
            chunks.append(current.strip())

        return chunks

    def get_stats(self) -> dict:
        """コレクションの統計情報を返す。"""
        return {
            "document_count": self.collection.count() if self.collection is not None else 0,
            "persist_dir": str(self.persist_dir),
        }
