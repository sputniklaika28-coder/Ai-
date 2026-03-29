"""config.py — 環境変数ベースの設定ローダー。

configs/.env から API キーやモデル名を読み込み、
Browser Use とローカル LLM の両方に設定を提供する。

初回起動時に configs/.env が存在しない場合は
configs/.env.example からテンプレートを自動生成する。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT / "configs" / ".env"
_ENV_EXAMPLE_PATH = _ROOT / "configs" / ".env.example"


def _ensure_env_file() -> None:
    """configs/.env が無ければ .env.example からコピーして案内を出す。"""
    if _ENV_PATH.exists():
        return
    if _ENV_EXAMPLE_PATH.exists():
        shutil.copy(_ENV_EXAMPLE_PATH, _ENV_PATH)
        print(
            "⚠️  configs/.env が見つからなかったため、"
            ".env.example からテンプレートを作成しました。",
            file=sys.stderr,
        )
    else:
        _ENV_PATH.touch()
        print(
            "⚠️  configs/.env を新規作成しました。",
            file=sys.stderr,
        )
    print(
        "   ➡ configs/.env を開いて設定を確認してください。\n"
        "   ➡ ローカル LLM のみで動作可能です（LM Studio 等）。\n"
        "   ➡ クラウド API を使う場合は OPENAI_API_KEY 等を設定してください。",
        file=sys.stderr,
    )


def load_config() -> dict[str, str]:
    """configs/.env を読み込み、設定辞書を返す。"""
    _ensure_env_file()
    load_dotenv(_ENV_PATH)
    return {
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "browser_use_model": os.getenv("BROWSER_USE_MODEL", ""),
        "browser_use_provider": os.getenv("BROWSER_USE_PROVIDER", "local"),
        "lm_studio_url": os.getenv("LM_STUDIO_URL", "http://localhost:1234"),
        "vlm_provider": os.getenv("VLM_PROVIDER", "local"),
        "vlm_model": os.getenv("VLM_MODEL", ""),
    }
