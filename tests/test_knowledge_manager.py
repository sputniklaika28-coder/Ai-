"""
test_knowledge_manager.py — KnowledgeManager のユニットテスト

ChromaDB と DuckDuckGo Search の外部依存はモックで差し替える。

テスト対象:
  - add_documents() / search_knowledge_base() — ベクトル検索
  - search_web() — ウェブ検索
  - ingest_world_setting() / ingest_session_log() — データ取り込み
  - _split_text() / _split_by_sentence() — テキスト分割
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.knowledge_manager import KnowledgeManager

# ──────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture
def km(tmp_path: Path) -> KnowledgeManager:
    """一時ディレクトリを使った KnowledgeManager"""
    return KnowledgeManager(
        persist_dir=str(tmp_path / "chroma_db"),
        collection_name="test_collection",
    )


@pytest.fixture
def world_setting_json(tmp_path: Path) -> Path:
    """テスト用の world_setting.json"""
    data = {
        "lore": "これはテスト用の世界観設定です。祓魔師たちが活躍する世界。",
        "rules": "戦闘はターン制で行われる。各ターンに1回の行動が可能。",
    }
    p = tmp_path / "world_setting.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def session_log_jsonl(tmp_path: Path) -> Path:
    """テスト用の chat_log.jsonl"""
    entries = [
        {"speaker": "GM", "body": "戦闘開始！敵が現れた。"},
        {"speaker": "スイレン", "body": "＞攻撃する！"},
        {"speaker": "GM", "body": "6ダメージを与えた。"},
    ]
    p = tmp_path / "chat_log.jsonl"
    p.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
        encoding="utf-8",
    )
    return p


# ──────────────────────────────────────────
# テキスト分割
# ──────────────────────────────────────────


class TestSplitText:
    def test_short_text_returns_single_chunk(self):
        text = "短いテキスト"
        chunks = KnowledgeManager._split_text(text, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_empty_text_returns_empty(self):
        assert KnowledgeManager._split_text("") == []
        assert KnowledgeManager._split_text("   ") == []

    def test_splits_on_paragraph_boundaries(self):
        # 各段落が chunk_size を超えるよう十分長いテキストを使う
        para1 = "段落1です。" * 20  # 100文字
        para2 = "段落2です。" * 20
        para3 = "段落3です。" * 20
        text = f"{para1}\n\n{para2}\n\n{para3}"
        chunks = KnowledgeManager._split_text(text, chunk_size=120, overlap=0)
        assert len(chunks) >= 2
        assert any("段落1" in c for c in chunks)
        assert any("段落3" in c for c in chunks)

    def test_respects_chunk_size(self):
        # 句読点を含むテキストで文末分割が効くようにする
        text = "。".join(["あいうえお" for _ in range(200)])
        chunks = KnowledgeManager._split_text(text, chunk_size=100, overlap=0)
        assert len(chunks) >= 2

    def test_splits_on_heading_markers(self):
        # 各セクションが chunk_size を超えるよう長くする
        text = "【セクション1】" + "内容1。" * 30 + "\n【セクション2】" + "内容2。" * 30
        chunks = KnowledgeManager._split_text(text, chunk_size=50, overlap=0)
        assert len(chunks) >= 2


class TestSplitBySentence:
    def test_splits_on_japanese_period(self):
        text = "文1です。文2です。文3です。文4です。文5です。"
        chunks = KnowledgeManager._split_by_sentence(text, chunk_size=12, overlap=0)
        assert len(chunks) >= 2

    def test_single_sentence(self):
        text = "一文のみ。"
        chunks = KnowledgeManager._split_by_sentence(text, chunk_size=100, overlap=0)
        assert len(chunks) == 1


# ──────────────────────────────────────────
# ドキュメント登録
# ──────────────────────────────────────────


class TestAddDocuments:
    def test_adds_documents_to_collection(self, km):
        count = km.add_documents(["テスト文書1", "テスト文書2"], source="test")
        assert count == 2
        assert km.collection.count() == 2

    def test_empty_list_returns_zero(self, km):
        assert km.add_documents([]) == 0

    def test_custom_metadata(self, km):
        metas = [{"source": "custom", "page": 1}]
        km.add_documents(["テスト"], metadatas=metas)
        results = km.collection.get(ids=["doc_0"])
        assert results["metadatas"][0]["source"] == "custom"


# ──────────────────────────────────────────
# ベクトル検索
# ──────────────────────────────────────────


class TestSearchKnowledgeBase:
    def test_returns_results(self, km):
        km.add_documents(["祓魔師の戦闘ルール", "キャラクター作成手順", "世界観の概要"])
        results = km.search_knowledge_base("戦闘")
        assert len(results) > 0
        assert "text" in results[0]
        assert "metadata" in results[0]
        assert "distance" in results[0]

    def test_empty_collection_returns_empty(self, km):
        results = km.search_knowledge_base("何か")
        assert results == []

    def test_respects_n_results(self, km):
        km.add_documents(["文書A", "文書B", "文書C", "文書D", "文書E"])
        results = km.search_knowledge_base("文書", n_results=2)
        assert len(results) <= 2


# ──────────────────────────────────────────
# ウェブ検索
# ──────────────────────────────────────────


class TestSearchWeb:
    @patch("duckduckgo_search.DDGS")
    def test_returns_formatted_results(self, mock_ddgs_cls, km):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = [
            {"title": "TRPG入門", "href": "https://example.com/trpg", "body": "TRPGの基本"},
            {"title": "CCFolia使い方", "href": "https://example.com/ccfolia", "body": "CCFoliaガイド"},
        ]
        mock_ddgs_cls.return_value = mock_ddgs

        results = km.search_web("TRPG CCFolia")
        assert len(results) == 2
        assert results[0]["title"] == "TRPG入門"
        assert results[0]["url"] == "https://example.com/trpg"
        assert results[0]["snippet"] == "TRPGの基本"

    @patch("duckduckgo_search.DDGS")
    def test_respects_max_results(self, mock_ddgs_cls, km):
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = [{"title": "A", "href": "http://a.com", "body": "a"}]
        mock_ddgs_cls.return_value = mock_ddgs

        results = km.search_web("test", max_results=1)
        mock_ddgs.text.assert_called_once_with("test", max_results=1)
        assert len(results) == 1

    @patch("duckduckgo_search.DDGS")
    def test_handles_error(self, mock_ddgs_cls, km):
        mock_ddgs_cls.side_effect = Exception("network error")
        results = km.search_web("test")
        assert results == []


# ──────────────────────────────────────────
# データ取り込み
# ──────────────────────────────────────────


class TestIngestWorldSetting:
    def test_ingests_world_setting(self, km, world_setting_json):
        count = km.ingest_world_setting(world_setting_json)
        assert count > 0
        assert km.collection.count() > 0

    def test_missing_file_returns_zero(self, km, tmp_path):
        count = km.ingest_world_setting(tmp_path / "nonexistent.json")
        assert count == 0


class TestIngestSessionLog:
    def test_ingests_session_log(self, km, session_log_jsonl):
        count = km.ingest_session_log(session_log_jsonl)
        assert count > 0
        assert km.collection.count() > 0

    def test_missing_file_returns_zero(self, km, tmp_path):
        count = km.ingest_session_log(tmp_path / "nonexistent.jsonl")
        assert count == 0

    def test_empty_file_returns_zero(self, km, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        count = km.ingest_session_log(p)
        assert count == 0


# ──────────────────────────────────────────
# 統計情報
# ──────────────────────────────────────────


class TestGetStats:
    def test_returns_stats(self, km):
        stats = km.get_stats()
        assert "document_count" in stats
        assert "persist_dir" in stats
        assert stats["document_count"] == 0

    def test_stats_update_after_add(self, km):
        km.add_documents(["テスト1", "テスト2"])
        stats = km.get_stats()
        assert stats["document_count"] == 2


# ──────────────────────────────────────────
# Phase 3: BM25 ハイブリッド検索テスト
# ──────────────────────────────────────────


class TestBM25HybridSearch:
    """BM25 ハイブリッド検索（rank_bm25 がある場合）のテスト。"""

    def test_stats_include_bm25_info(self, km):
        stats = km.get_stats()
        assert "bm25_available" in stats
        assert "bm25_corpus_size" in stats
        assert "hybrid_search" in stats

    def test_bm25_corpus_grows_with_add(self, km):
        if not km._bm25_available:
            pytest.skip("rank_bm25 が未インストール")
        km.add_documents(["文書A 固有名詞テスト", "文書B 別のテスト"])
        assert len(km._bm25_corpus) == 2

    def test_hybrid_search_returns_results(self, km):
        """ハイブリッド検索が結果を返すことを確認。"""
        if not km._bm25_available:
            pytest.skip("rank_bm25 が未インストール")
        km.add_documents([
            "ファイアボールは炎属性の呪文。範囲4マス。ダメージ3d6。",
            "アイスランスは氷属性。単体攻撃。ダメージ2d8。",
            "キャラクターのHPは最大20。",
        ])
        results = km.search_knowledge_base("ファイアボール")
        assert len(results) > 0
        # ファイアボールを含む結果が返ってくる
        texts = [r["text"] for r in results]
        assert any("ファイアボール" in t for t in texts)

    def test_hybrid_search_api_signature_unchanged(self, km):
        """ハイブリッド化後も既存 API が変わらないことを確認。"""
        km.add_documents(["テストドキュメント"])
        results = km.search_knowledge_base("テスト", n_results=3)
        assert isinstance(results, list)
        if results:
            assert "text" in results[0]
            assert "metadata" in results[0]
            assert "distance" in results[0]

    def test_bm25_finds_keyword_not_in_vector_top(self, km):
        """キーワード一致がベクトル検索を補完する確認。"""
        if not km._bm25_available:
            pytest.skip("rank_bm25 が未インストール")
        km.add_documents([
            "セーブデータのバックアップ方法について",
            "TRPGのキャラクターを作成する手順と注意点",
            "祓魔師コード247: 特殊暗号認証プロトコル",  # 固有名詞
        ])
        results = km.search_knowledge_base("祓魔師コード247", n_results=3)
        texts = [r["text"] for r in results]
        # 固有名詞を含む文書が上位に来ていることを確認
        assert any("247" in t for t in texts[:2])


class TestContextualChunking:
    """Contextual Chunking (ingest_world_setting の改善) のテスト。"""

    def test_ingested_chunks_contain_section_header(self, km, tmp_path):
        """登録されたチャンクにセクションヘッダが付与されていることを確認。"""
        data = {
            "魔法システム": "ファイアボールは炎系攻撃魔法。MP消費5。",
            "戦闘ルール": "行動順はイニシアチブ値で決まる。",
        }
        p = tmp_path / "ws.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        km.ingest_world_setting(p)

        # コレクションを直接確認
        if km.collection:
            all_docs = km.collection.get()
            docs_text = all_docs.get("documents", [])
            # 少なくとも1つのチャンクにセクションヘッダが付いている
            assert any("【" in d for d in docs_text)
            assert any("魔法システム" in d or "戦闘ルール" in d for d in docs_text)

    def test_ingest_rulebook_text_file(self, km, tmp_path):
        """テキストファイルのルールブック取り込みテスト。"""
        rulebook = tmp_path / "rules.txt"
        rulebook.write_text(
            "第1章: 基本ルール\n祓魔師は毎ターン1回行動できる。\n\n第2章: 戦闘\n戦闘は10フェイズ制。",
            encoding="utf-8",
        )
        count = km.ingest_rulebook(rulebook, source_name="test_rulebook")
        assert count > 0

    def test_tokenize_ja_extracts_cjk_ngrams(self):
        """日本語トークナイザーが CJK 2-gram を正しく生成する。"""
        from core.knowledge_manager import _tokenize_ja
        tokens = _tokenize_ja("ファイアボール")
        # 2-gram が含まれているか
        assert "ファイ" in tokens or "イア" in tokens or "アボ" in tokens

    def test_tokenize_ja_extracts_ascii(self):
        """英数字が正しく抽出される。"""
        from core.knowledge_manager import _tokenize_ja
        tokens = _tokenize_ja("HP=20 MP=10")
        assert "hp" in tokens or "mp" in tokens
