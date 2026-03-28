"""config.py — 環境変数ベースの設定ローダー。

configs/.env から API キーやモデル名を読み込み、
Browser Use とローカル LLM の両方に設定を提供する。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT / "configs" / ".env"


def load_config() -> dict[str, str]:
    """configs/.env を読み込み、設定辞書を返す。"""
    load_dotenv(_ENV_PATH)
    return {
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "browser_use_model": os.getenv("BROWSER_USE_MODEL", "gpt-4o"),
        "lm_studio_url": os.getenv("LM_STUDIO_URL", "http://localhost:1234"),
        "vlm_provider": os.getenv("VLM_PROVIDER", "openai"),
        "vlm_model": os.getenv("VLM_MODEL", "gpt-4o"),
    }
