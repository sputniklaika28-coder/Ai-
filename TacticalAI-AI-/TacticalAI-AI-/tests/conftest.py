"""
conftest.py — 共通フィクスチャ

全テストで共有する pytest フィクスチャをここに集約する。
外部依存（LM Studio API / Selenium）はすべてモック化する。
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# core/ を import できるようにパスを追加
sys.path.insert(0, str(Path(__file__).parent.parent))


# ──────────────────────────────────────────
# JSON ファイルフィクスチャ
# ──────────────────────────────────────────


@pytest.fixture
def characters_json(tmp_path: Path) -> Path:
    """最小構成の characters.json を一時ディレクトリに作る"""
    data = {
        "characters": {
            "meta_gm": {
                "id": "meta_gm",
                "name": "ゲームマスター",
                "layer": "meta",
                "role": "game_master",
                "enabled": True,
                "is_ai": True,
                "prompt_id": "meta_gm_template",
            },
            "player_01": {
                "id": "player_01",
                "name": "スイレン",
                "layer": "player",
                "role": "player",
                "enabled": False,
                "is_ai": False,
                "prompt_id": "player_template",
            },
        }
    }
    p = tmp_path / "characters.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


@pytest.fixture
def prompts_json(tmp_path: Path) -> Path:
    """最小構成の prompts.json を一時ディレクトリに作る"""
    data = {
        "templates": {
            "meta_gm_template": {
                "system": "あなたはGMです。",
                "user_template": "{user_input}",
            }
        }
    }
    p = tmp_path / "prompts.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ──────────────────────────────────────────
# LMClient モック
# ──────────────────────────────────────────


@pytest.fixture
def mock_lm_client():
    """LMClient を完全にモックした MagicMock を返す"""
    client = MagicMock()
    client.is_server_running.return_value = True
    client.generate_response.return_value = ('{"action": "move", "target": "A1"}', None)
    return client


# ──────────────────────────────────────────
# VTTアダプター モック
# ──────────────────────────────────────────


@pytest.fixture
def mock_adapter():
    """BaseVTTAdapter を模倣する MagicMock"""
    adapter = MagicMock()
    adapter.get_board_state.return_value = [
        {
            "index": 0,
            "img_hash": "abcdef12",
            "img_url": "https://example.com/files/abcdef12/img.png",
            "px_x": 96,
            "px_y": 192,
            "grid_x": 1,
            "grid_y": 2,
        }
    ]
    adapter.move_piece.return_value = True
    adapter.spawn_piece.return_value = True
    adapter.send_chat.return_value = True
    adapter.get_chat_messages.return_value = []
    adapter.take_screenshot.return_value = b"\x89PNG"
    return adapter


@pytest.fixture
def mock_driver():
    """Selenium WebDriver を模倣する MagicMock（レガシー互換用）"""
    driver = MagicMock()
    driver.execute_script.return_value = [
        {
            "index": 0,
            "transform": "translate(96px, 192px)",
            "imgSrc": "https://example.com/files/abcdef12/img.png",
            "vx": 150.0,
            "vy": 250.0,
        }
    ]
    return driver
