"""coord_cache.py のユニットテスト。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from addons.vlm_os_agent.coord_cache import CoordCache


@pytest.fixture
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / "coords.json"


class TestExactMatch:
    def test_put_and_get(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600)
        c.put("ココフォリア", "abcd1234", "送信ボタン", (100, 200))
        got = c.get("ココフォリア", "abcd1234", "送信ボタン")
        assert got == (100, 200)

    def test_miss(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600)
        assert c.get("win", "aaaa", "desc") is None

    def test_hits_increment(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600)
        c.put("w", "ffff", "d", (1, 2))
        c.get("w", "ffff", "d")
        c.get("w", "ffff", "d")
        entries = c.all_entries()
        assert entries[0]["hits"] == 2


class TestApproximateMatch:
    def test_phash_tolerance_hit(self, cache_path: Path) -> None:
        # pHash 16 進: "ff00" と "ff01" は 1 ビット差 → tolerance=6 でヒット
        c = CoordCache(cache_path, ttl_seconds=3600, phash_tolerance=6)
        c.put("w", "ff00", "desc", (10, 20))
        got = c.get("w", "ff01", "desc")
        assert got == (10, 20)

    def test_phash_tolerance_miss(self, cache_path: Path) -> None:
        # 全ビット差（0000 vs ffff = 16 ビット）は tolerance=6 で外れる
        c = CoordCache(cache_path, ttl_seconds=3600, phash_tolerance=6)
        c.put("w", "0000", "desc", (10, 20))
        assert c.get("w", "ffff", "desc") is None

    def test_different_window_no_match(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600, phash_tolerance=6)
        c.put("w1", "ff00", "desc", (10, 20))
        assert c.get("w2", "ff01", "desc") is None

    def test_different_description_no_match(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600, phash_tolerance=6)
        c.put("w", "ff00", "a", (10, 20))
        assert c.get("w", "ff01", "b") is None


class TestTtl:
    def test_expired_entry_pruned(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=1)
        c.put("w", "ff00", "d", (1, 2))
        # created_at を人工的に 10 秒前に書き換え
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        for entry in raw["entries"].values():
            entry["created_at"] = time.time() - 10
        cache_path.write_text(json.dumps(raw), encoding="utf-8")

        c2 = CoordCache(cache_path, ttl_seconds=1)
        assert c2.get("w", "ff00", "d") is None

    def test_ttl_disabled_by_zero(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=0)
        c.put("w", "ff00", "d", (1, 2))
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        for entry in raw["entries"].values():
            entry["created_at"] = time.time() - 100_000
        cache_path.write_text(json.dumps(raw), encoding="utf-8")

        c2 = CoordCache(cache_path, ttl_seconds=0)
        assert c2.get("w", "ff00", "d") == (1, 2)

    def test_prune(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=1)
        c.put("w", "ff00", "a", (1, 2))
        c.put("w", "ff11", "b", (3, 4))
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        old_time = time.time() - 100
        for entry in raw["entries"].values():
            entry["created_at"] = old_time
        cache_path.write_text(json.dumps(raw), encoding="utf-8")
        c2 = CoordCache(cache_path, ttl_seconds=1)
        removed = c2.prune()
        assert removed == 2
        assert c2.all_entries() == []


class TestInvalidate:
    def test_invalidate_by_key(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600)
        key = c.put("w", "ff00", "d", (1, 2))
        assert c.invalidate(key) is True
        assert c.get("w", "ff00", "d") is None

    def test_invalidate_match_exact(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600, phash_tolerance=6)
        c.put("w", "ff00", "d", (1, 2))
        assert c.invalidate_match("w", "ff00", "d") is True
        assert c.get("w", "ff00", "d") is None

    def test_invalidate_match_approx(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600, phash_tolerance=6)
        c.put("w", "ff00", "d", (1, 2))
        # 近似一致も破棄される
        assert c.invalidate_match("w", "ff01", "d") is True
        assert c.get("w", "ff00", "d") is None


class TestPersistence:
    def test_roundtrip_on_disk(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600)
        c.put("w", "ff00", "d", (10, 20))
        # 別インスタンスで読む
        c2 = CoordCache(cache_path, ttl_seconds=3600)
        assert c2.get("w", "ff00", "d") == (10, 20)

    def test_corrupt_file_yields_empty(self, cache_path: Path) -> None:
        cache_path.write_text("not json", encoding="utf-8")
        c = CoordCache(cache_path, ttl_seconds=3600)
        assert c.all_entries() == []

    def test_clear(self, cache_path: Path) -> None:
        c = CoordCache(cache_path, ttl_seconds=3600)
        c.put("w", "ff00", "d", (1, 2))
        c.clear()
        assert c.all_entries() == []
