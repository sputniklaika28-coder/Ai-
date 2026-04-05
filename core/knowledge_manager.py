"""knowledge_manager.py — RAG（ハイブリッド検索）+ Web検索 統合マネージャー。

ChromaDB ベクトル検索 と BM25 キーワード検索を組み合わせたハイブリッド検索を提供する。
Reciprocal Rank Fusion (RRF) でランキングを統合し、固有名詞の取りこぼしを防ぐ。

BM25 が未インストールの場合は従来のベクトル検索のみにフォールバックする。

Phase 3 改善:
- ハイブリッド検索: ChromaDB（意味的類似）+ BM25（キーワード一致）
- Reciprocal Rank Fusion: 両ランキングを融合して精度向上
- Contextual Chunking: チャンク登録時にセクションヘッダを付与
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

# RRF パラメータ
_RRF_K = 60  # RRF の定数（60 が一般的な推奨値）


def _tokenize_ja(text: str) -> list[str]:
    """日本語テキストを文字 n-gram + 英数字単語でトークナイズする。

    mecab 等が不要なシンプルな実装:
    - 英数字・記号は単語単位
    - 漢字/仮名は 2-gram でトークナイズ
    固有名詞（「ファイアボール」等）の取りこぼしを大幅に減らす。
    """
    tokens: list[str] = []
    # 英数字単語
    for m in re.finditer(r"[a-zA-Z0-9]+", text):
        tokens.append(m.group().lower())
    # 日本語（CJK + 仮名）の 2-gram
    cjk_chars = re.sub(r"[^\u3000-\u9fff\uff00-\uffef]", "", text)
    for i in range(len(cjk_chars) - 1):
        tokens.append(cjk_chars[i : i + 2])
    return tokens


class KnowledgeManager:
    """RAG ハイブリッド検索 + ウェブ検索を提供するナレッジマネージャー。

    ベクトル検索 (ChromaDB) と BM25 キーワード検索を組み合わせ、
    Reciprocal Rank Fusion で結果をマージして高精度な検索を実現する。

    Attributes:
        client: ChromaDB の永続化クライアント。
        collection: ドキュメントを格納する ChromaDB コレクション。
    """

    def __init__(
        self,
        persist_dir: str = "data/chroma_db",
        collection_name: str = "tactical_ai_knowledge",
        use_hybrid: bool = True,
    ) -> None:
        """KnowledgeManager を初期化する。

        Args:
            persist_dir: ChromaDB の永続化ディレクトリ。
            collection_name: 使用するコレクション名。
            use_hybrid: True の場合 BM25 ハイブリッド検索を試みる。
        """
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._use_hybrid = use_hybrid

        self.client = None
        self.collection = None

        # BM25 インデックス（インメモリ）
        self._bm25 = None            # BM25Okapi インスタンス
        self._bm25_corpus: list[str] = []  # 登録済みドキュメント（生テキスト）

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

        # BM25 ライブラリのロード試行
        self._bm25_available = False
        if use_hybrid:
            try:
                from rank_bm25 import BM25Okapi  # noqa: F401
                self._bm25_available = True
                logger.info("BM25 ハイブリッド検索が有効です")
            except ImportError:
                logger.info(
                    "rank-bm25 が未インストールです。ベクトル検索のみで動作します。"
                    " pip install rank-bm25 でハイブリッド検索が有効になります。"
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
        """テキストをベクトルDB（と BM25 インデックス）に登録する。

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

        # BM25 コーパスに追加してインデックスを再構築
        if self._bm25_available:
            self._bm25_corpus.extend(texts)
            self._rebuild_bm25()

        return len(texts)

    def _rebuild_bm25(self) -> None:
        """BM25 インデックスを全コーパスで再構築する。"""
        if not self._bm25_available or not self._bm25_corpus:
            return
        try:
            from rank_bm25 import BM25Okapi
            tokenized = [_tokenize_ja(doc) for doc in self._bm25_corpus]
            self._bm25 = BM25Okapi(tokenized)
        except Exception as e:
            logger.warning("BM25 インデックス構築エラー: %s", e)
            self._bm25 = None

    # ──────────────────────────────────────────
    # 検索
    # ──────────────────────────────────────────

    def search_knowledge_base(self, query: str, n_results: int = 5) -> list[dict]:
        """ハイブリッド検索でドキュメントを検索する。

        BM25 が利用可能な場合:
          ベクトル検索 + BM25 → Reciprocal Rank Fusion で統合

        BM25 が利用できない場合:
          従来のベクトル類似検索のみ

        Args:
            query: 検索クエリテキスト。
            n_results: 返す結果の最大数。

        Returns:
            検索結果のリスト。各要素は以下のキーを含む:
            - text: str — マッチしたテキスト
            - metadata: dict — メタデータ
            - distance: float — コサイン距離（ハイブリッド時は 0.0）
            - score: float — RRF スコア（ハイブリッド時のみ）
        """
        if self.collection is None:
            logger.warning("ChromaDB 未初期化のため検索をスキップしました。")
            return []

        if self.collection.count() == 0:
            logger.warning("コレクションが空です。先にドキュメントを登録してください。")
            return []

        # ハイブリッド検索
        if self._bm25_available and self._bm25 is not None and self._bm25_corpus:
            return self._hybrid_search(query, n_results)
        else:
            return self._vector_search(query, n_results)

    def _vector_search(self, query: str, n_results: int) -> list[dict]:
        """ChromaDB のみによるベクトル類似検索。"""
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

    def _hybrid_search(self, query: str, n_results: int) -> list[dict]:
        """ベクトル検索 + BM25 を RRF でマージするハイブリッド検索。"""
        fetch_k = min(n_results * 3, self.collection.count())

        # 1. ベクトル検索（多めに取得）
        vec_results = self.collection.query(
            query_texts=[query],
            n_results=fetch_k,
        )

        vec_docs: list[str] = []
        vec_metas: list[dict] = []
        vec_dists: list[float] = []
        if vec_results and vec_results["documents"]:
            vec_docs = vec_results["documents"][0]
            vec_metas = vec_results["metadatas"][0] if vec_results["metadatas"] else [{}] * len(vec_docs)
            vec_dists = vec_results["distances"][0] if vec_results["distances"] else [0.0] * len(vec_docs)

        # 2. BM25 検索（コーパス全体に対して）
        query_tokens = _tokenize_ja(query)
        bm25_scores = self._bm25.get_scores(query_tokens)
        bm25_ranked = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)
        bm25_top = bm25_ranked[: fetch_k]

        # 3. Reciprocal Rank Fusion
        rrf_scores: dict[str, float] = {}
        doc_map: dict[str, dict] = {}  # text -> {text, metadata, distance}

        # ベクトル検索のランキングを RRF に組み込む
        for rank, (doc, meta, dist) in enumerate(zip(vec_docs, vec_metas, vec_dists)):
            key = doc[:100]  # 先頭100文字をキーとして使用
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
            doc_map[key] = {"text": doc, "metadata": meta, "distance": dist, "score": 0.0}

        # BM25 のランキングを RRF に組み込む
        for rank, corpus_idx in enumerate(bm25_top):
            if corpus_idx >= len(self._bm25_corpus):
                continue
            doc = self._bm25_corpus[corpus_idx]
            key = doc[:100]
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
            if key not in doc_map:
                doc_map[key] = {"text": doc, "metadata": {}, "distance": 0.0, "score": 0.0}

        # 4. RRF スコアでソートして上位 n_results を返す
        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)
        output: list[dict] = []
        for key in sorted_keys[:n_results]:
            entry = dict(doc_map[key])
            entry["score"] = rrf_scores[key]
            output.append(entry)

        logger.debug(
            "ハイブリッド検索完了: query=%r, vec=%d件, bm25=%d件, merged=%d件",
            query[:30],
            len(vec_docs),
            len(bm25_top),
            len(output),
        )
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
    # データ取り込み（Contextual Chunking）
    # ──────────────────────────────────────────

    def ingest_world_setting(self, path: str | Path) -> int:
        """world_setting.json をコンテキスト付きチャンクに分割してベクトルDBに登録する。

        Contextual Chunking: 各チャンクにセクションキー（章タイトル）を付与し、
        固有名詞検索時の取りこぼしを防ぐ。

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

            all_chunks: list[str] = []
            all_metas: list[dict] = []

            for section_key, section_text in data.items():
                if not isinstance(section_text, str) or not section_text:
                    continue

                raw_chunks = self._split_text(section_text)
                for i, chunk in enumerate(raw_chunks):
                    # コンテキストヘッダを付与（セクション名 + チャンク内容）
                    contextual_chunk = f"【{section_key}】\n{chunk}"
                    all_chunks.append(contextual_chunk)
                    all_metas.append({
                        "source": "world_setting",
                        "section": section_key,
                        "chunk_index": i,
                    })

            return self.add_documents(
                texts=all_chunks,
                metadatas=all_metas,
                source="world_setting",
            )
        except Exception as e:
            logger.error("世界観データ取り込みエラー: %s", e)
            return 0

    def ingest_rulebook(self, path: str | Path, source_name: str = "rulebook") -> int:
        """テキストファイル（ルールブック等）をチャンクに分割して登録する。

        Args:
            path: テキストファイルのパス。
            source_name: メタデータの source 値。

        Returns:
            登録されたチャンク数。
        """
        path = Path(path)
        if not path.exists():
            logger.warning("ファイルが見つかりません: %s", path)
            return 0

        try:
            text = path.read_text(encoding="utf-8")
            chunks = self._split_text(text)
            metas = [{"source": source_name, "chunk_index": i} for i in range(len(chunks))]
            return self.add_documents(texts=chunks, metadatas=metas, source=source_name)
        except Exception as e:
            logger.error("ルールブック取り込みエラー: %s", e)
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

            # セッションログは会話の流れが重要なので、複数エントリをまとめてチャンク化
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
            "bm25_available": self._bm25_available,
            "bm25_corpus_size": len(self._bm25_corpus),
            "hybrid_search": self._bm25_available and self._bm25 is not None,
        }
