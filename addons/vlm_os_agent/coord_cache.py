"""coord_cache.py — 座標キャッシュ（pHash キー + TTL + ディスク永続化）。

VLM 呼び出しコスト削減のために、一度特定した UI 要素の座標を
(window_title, phash, description) キーで保存する。

キャッシュの既定挙動は OFF（呼び出し側が明示的に `use_cache=True` で有効化）。
本クラスはインフラのみを提供し、キャッシュを使うかどうかは呼び出し側の判断。

ファイル形式: `{addon_dir}/cache/coords.json`
  {
    "entries": {
      "<sha1_key>": {
        "window_title": "...",
        "phash": "...",
        "description": "...",
        "px_x": 123,
        "px_y": 456,
        "created_at": 1700000000.0,
        "hits": 3
      },
      ...
    }
  }
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from core.vision_utils import phash_distance

logger = logging.getLogger(__name__)


def _make_key(window_title: str, phash: str, description: str) -> str:
    """キャッシュキーを生成する（SHA1）。"""
    raw = f"{window_title}|{phash}|{description}".encode()
    return hashlib.sha1(raw).hexdigest()


class CoordCache:
    """座標キャッシュ（ディスク永続化 + pHash ハミング距離マッチ）。"""

    def __init__(
        self,
        path: Path,
        ttl_seconds: int = 3600,
        *,
        phash_tolerance: int = 6,
    ) -> None:
        self._path = Path(path)
        self._ttl = int(ttl_seconds)
        self._tolerance = int(phash_tolerance)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ──────────────────────────────────────
    # 読み書き
    # ──────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            entries = raw.get("entries") if isinstance(raw, dict) else None
            self._data = entries if isinstance(entries, dict) else {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("座標キャッシュ読み込み失敗 (%s): %s", self._path, e)
            self._data = {}

    def _atomic_write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".coords_", suffix=".json", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"entries": self._data}, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ──────────────────────────────────────
    # 公開 API
    # ──────────────────────────────────────

    def get(
        self,
        window_title: str,
        phash: str,
        description: str,
    ) -> tuple[int, int] | None:
        """キャッシュヒット時は (px_x, px_y) を返す。

        1) 完全一致キーをまず試す
        2) 失敗時は同 (window_title, description) の中で
           pHash ハミング距離 <= tolerance の最近傍を探す
        TTL 超過エントリは破棄。
        """
        with self._lock:
            now = time.time()
            key = _make_key(window_title, phash, description)

            entry = self._data.get(key)
            if entry is not None:
                if self._is_expired(entry, now):
                    self._data.pop(key, None)
                else:
                    entry["hits"] = int(entry.get("hits", 0)) + 1
                    return (int(entry["px_x"]), int(entry["px_y"]))

            # 近似一致の探索
            best: tuple[int, int] | None = None
            best_dist = self._tolerance + 1
            best_key: str | None = None
            for k, e in list(self._data.items()):
                if e.get("window_title") != window_title:
                    continue
                if e.get("description") != description:
                    continue
                if self._is_expired(e, now):
                    self._data.pop(k, None)
                    continue
                dist = phash_distance(str(e.get("phash", "")), phash)
                if dist <= self._tolerance and dist < best_dist:
                    best_dist = dist
                    best = (int(e["px_x"]), int(e["px_y"]))
                    best_key = k
            if best is not None and best_key is not None:
                self._data[best_key]["hits"] = int(
                    self._data[best_key].get("hits", 0)
                ) + 1
                return best
            return None

    def put(
        self,
        window_title: str,
        phash: str,
        description: str,
        coords: tuple[int, int],
    ) -> str:
        """キャッシュに座標を登録し、キーを返す。"""
        with self._lock:
            key = _make_key(window_title, phash, description)
            self._data[key] = {
                "window_title": window_title,
                "phash": phash,
                "description": description,
                "px_x": int(coords[0]),
                "px_y": int(coords[1]),
                "created_at": time.time(),
                "hits": 0,
            }
            try:
                self._atomic_write()
            except OSError as e:
                logger.warning("座標キャッシュ書き込み失敗: %s", e)
            return key

    def invalidate(self, key: str) -> bool:
        """キーを明示破棄する。存在したら True。"""
        with self._lock:
            if key in self._data:
                self._data.pop(key, None)
                try:
                    self._atomic_write()
                except OSError as e:
                    logger.warning("座標キャッシュ書き込み失敗: %s", e)
                return True
            return False

    def invalidate_match(
        self, window_title: str, phash: str, description: str
    ) -> bool:
        """指定 3 要素のキーを破棄する。ヒットなし時は近似一致エントリも破棄。"""
        key = _make_key(window_title, phash, description)
        with self._lock:
            removed = False
            if key in self._data:
                self._data.pop(key, None)
                removed = True
            for k, e in list(self._data.items()):
                if e.get("window_title") != window_title:
                    continue
                if e.get("description") != description:
                    continue
                dist = phash_distance(str(e.get("phash", "")), phash)
                if dist <= self._tolerance:
                    self._data.pop(k, None)
                    removed = True
            if removed:
                try:
                    self._atomic_write()
                except OSError as e:
                    logger.warning("座標キャッシュ書き込み失敗: %s", e)
            return removed

    def prune(self) -> int:
        """TTL 超過エントリを一掃し、削除数を返す。"""
        with self._lock:
            now = time.time()
            before = len(self._data)
            self._data = {
                k: e for k, e in self._data.items() if not self._is_expired(e, now)
            }
            removed = before - len(self._data)
            if removed:
                try:
                    self._atomic_write()
                except OSError as e:
                    logger.warning("座標キャッシュ書き込み失敗: %s", e)
            return removed

    def all_entries(self) -> list[dict[str, Any]]:
        """GUI 表示用に全エントリをスナップショット。"""
        with self._lock:
            return [dict(key=k, **e) for k, e in self._data.items()]

    def clear(self) -> None:
        with self._lock:
            self._data = {}
            try:
                self._atomic_write()
            except OSError as e:
                logger.warning("座標キャッシュ書き込み失敗: %s", e)

    # ──────────────────────────────────────
    # 内部ヘルパ
    # ──────────────────────────────────────

    def _is_expired(self, entry: dict[str, Any], now: float) -> bool:
        if self._ttl <= 0:
            return False
        try:
            return now - float(entry.get("created_at", 0.0)) > self._ttl
        except (TypeError, ValueError):
            return True
