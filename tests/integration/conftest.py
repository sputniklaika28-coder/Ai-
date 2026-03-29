"""
conftest.py — 統合テスト共通フィクスチャ

実環境（CCFolia + LLM + ブラウザ）を使用する統合テスト用。
環境変数 CCFOLIA_ROOM_URL が設定されていない場合、全テストをスキップする。

前提条件:
  - LM Studio が localhost:1234 で起動している
  - configs/.env に有効な API キーが設定されている
  - playwright install chromium が完了している
  - 環境変数 CCFOLIA_ROOM_URL にテスト用ルームURLが設定されている
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# リポジトリルートを import パスに追加
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))


def pytest_collection_modifyitems(config, items):
    """CCFOLIA_ROOM_URL が未設定の場合、統合テストを全スキップ。"""
    room_url = os.environ.get("CCFOLIA_ROOM_URL", "")
    if not room_url:
        skip = pytest.mark.skip(reason="CCFOLIA_ROOM_URL が未設定")
        for item in items:
            if "integration" in str(item.fspath):
                item.add_marker(skip)


# ──────────────────────────────────────────
# 設定フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture(scope="session")
def room_url():
    """テスト用 CCFolia ルーム URL。"""
    url = os.environ.get("CCFOLIA_ROOM_URL", "")
    if not url:
        pytest.skip("CCFOLIA_ROOM_URL が未設定")
    return url


@pytest.fixture(scope="session")
def app_config():
    """アプリケーション設定を読み込む。"""
    from core.config import load_config
    return load_config()


@pytest.fixture(scope="session")
def provider_and_key(app_config):
    """Browser Use のプロバイダーと API キーを返す。"""
    provider = app_config.get("browser_use_provider", "local")
    api_key = ""
    if provider == "anthropic":
        api_key = app_config.get("anthropic_api_key", "")
    elif provider == "openai":
        api_key = app_config.get("openai_api_key", "")
    return provider, api_key


# ──────────────────────────────────────────
# Browser Use アダプター（セッションスコープ）
# ──────────────────────────────────────────


@pytest.fixture(scope="session")
def adapter(room_url, app_config, provider_and_key):
    """BrowserUseVTTAdapter を初期化し、ルームに接続する。

    セッション全体で共有するため scope="session" とする。
    """
    from core.vtt_adapters.browser_use_adapter import BrowserUseVTTAdapter

    provider, api_key = provider_and_key
    model = app_config.get("browser_use_model", "")
    lm_studio_url = app_config.get("lm_studio_url", "http://localhost:1234")

    adp = BrowserUseVTTAdapter(
        model_name=model,
        api_key=api_key,
        provider=provider,
        headless=bool(os.environ.get("HEADLESS", "")),
        lm_studio_url=lm_studio_url,
    )
    adp.connect(room_url)
    yield adp
    adp.close()


# ──────────────────────────────────────────
# テスト用アセットファイル
# ──────────────────────────────────────────

_ASSETS_DIR = _ROOT / "tests" / "integration" / "assets"


@pytest.fixture(scope="session")
def test_png(tmp_path_factory) -> Path:
    """1x1 の最小テスト用 PNG ファイルを生成する。"""
    d = tmp_path_factory.mktemp("assets")
    p = d / "test_bg.png"
    # 最小の有効 PNG (1x1 赤ピクセル)
    import struct
    import zlib

    def _make_png():
        signature = b"\x89PNG\r\n\x1a\n"

        def chunk(chunk_type, data):
            c = chunk_type + data
            crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
            return struct.pack(">I", len(data)) + c + crc

        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        raw = zlib.compress(b"\x00\xff\x00\x00")
        return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")

    p.write_bytes(_make_png())
    return p


@pytest.fixture(scope="session")
def test_mp3(tmp_path_factory) -> Path:
    """最小テスト用 MP3 ファイル（ダミー）を生成する。"""
    d = tmp_path_factory.mktemp("assets")
    p = d / "test_bgm.mp3"
    # MP3 フレームヘッダー (MPEG1 Layer3 128kbps 44100Hz stereo) + 無音データ
    frame_header = b"\xff\xfb\x90\x00"
    p.write_bytes(frame_header + b"\x00" * 417)  # 1 frame
    return p


@pytest.fixture(scope="session")
def nonexistent_mp3() -> str:
    """存在しない BGM パス（エラーリカバリーテスト用）。"""
    return "/tmp/nonexistent_bgm_for_test.mp3"


# ──────────────────────────────────────────
# シーン定義フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture
def scenes_json(tmp_path) -> Path:
    """テスト用 scenes.json を生成する。"""
    data = {
        "scenes": [
            {
                "name": "酒場",
                "description": "冒険者が集まる酒場",
                "background_image": "",
                "bgm": [],
                "characters": [],
                "metadata": {"mood": "calm"},
            },
            {
                "name": "ダンジョン",
                "description": "暗い地下迷宮",
                "background_image": "",
                "bgm": [],
                "characters": [],
                "metadata": {"mood": "tense"},
            },
        ]
    }
    p = tmp_path / "scenes.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
