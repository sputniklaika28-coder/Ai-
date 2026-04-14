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


# ──────────────────────────────────────────
# VLM OS Agent 用 env ゲッター
# ──────────────────────────────────────────

_VLM_AGENT_TRUE = {"1", "true", "yes", "on"}


def _vlm_agent_env_loaded() -> None:
    """.env が未ロードなら load_dotenv する（呼び出しは副作用のみ）。"""
    if not os.environ.get("_VLM_AGENT_ENV_LOADED"):
        _ensure_env_file()
        load_dotenv(_ENV_PATH)
        os.environ["_VLM_AGENT_ENV_LOADED"] = "1"


def get_vlm_agent_target_window() -> str:
    """対象ウィンドウのタイトル（正規表現）。既定 'ココフォリア'。"""
    _vlm_agent_env_loaded()
    return os.getenv("VLM_AGENT_TARGET_WINDOW", "ココフォリア")


def get_vlm_agent_poll_ms() -> int:
    """エージェントループのポーリング間隔（ミリ秒）。既定 500。"""
    _vlm_agent_env_loaded()
    try:
        return max(50, int(os.getenv("VLM_AGENT_POLL_MS", "500")))
    except ValueError:
        return 500


def get_vlm_agent_cache_ttl() -> int:
    """座標キャッシュ TTL（秒）。既定 3600。"""
    _vlm_agent_env_loaded()
    try:
        return max(0, int(os.getenv("VLM_AGENT_CACHE_TTL", "3600")))
    except ValueError:
        return 3600


def get_vlm_agent_som_enabled() -> bool:
    """Set-of-Mark 番号札を有効化するか。既定 False。"""
    _vlm_agent_env_loaded()
    return os.getenv("VLM_AGENT_SOM_ENABLED", "false").strip().lower() in _VLM_AGENT_TRUE


def get_vlm_agent_max_steps() -> int:
    """os_run_task の最大ステップ数。既定 20。"""
    _vlm_agent_env_loaded()
    try:
        return max(1, int(os.getenv("VLM_AGENT_MAX_STEPS", "20")))
    except ValueError:
        return 20


def get_vlm_agent_failsafe() -> bool:
    """pyautogui の FAILSAFE（画面四隅緊急停止）を有効化するか。既定 True。"""
    _vlm_agent_env_loaded()
    return os.getenv("VLM_AGENT_FAILSAFE", "true").strip().lower() in _VLM_AGENT_TRUE


def get_vlm_agent_perceive_backend() -> str:
    """Perceive バックエンド名: 'none' | 'cv' | 'omniparser'。既定 'none'。"""
    _vlm_agent_env_loaded()
    v = os.getenv("VLM_AGENT_PERCEIVE_BACKEND", "none").strip().lower()
    if v not in {"none", "cv", "omniparser"}:
        return "none"
    return v


# ──────────────────────────────────────────
# VTT バックエンド選択
# ──────────────────────────────────────────


def get_vtt_backend() -> str:
    """VTT バックエンド名: 'ccfolia' | 'foundry' | 'vision'。既定 'ccfolia'。"""
    _vlm_agent_env_loaded()
    v = os.getenv("VTT_BACKEND", "ccfolia").strip().lower()
    if v not in {"ccfolia", "foundry", "vision"}:
        return "ccfolia"
    return v


# ──────────────────────────────────────────
# Foundry VTT アダプター設定
# ──────────────────────────────────────────


def get_foundry_url() -> str:
    """Foundry VTT サーバー URL。既定 'http://localhost:30000'。"""
    _vlm_agent_env_loaded()
    return os.getenv("FOUNDRY_URL", "http://localhost:30000").rstrip("/")


def get_foundry_api_key() -> str:
    """Foundry VTT API キー（foundryvtt-rest-api モジュール用）。"""
    _vlm_agent_env_loaded()
    return os.getenv("FOUNDRY_API_KEY", "")


# ──────────────────────────────────────────
# VisionVTT アダプター設定
# ──────────────────────────────────────────


def get_vision_vtt_window() -> str:
    """VisionVTT の対象ウィンドウタイトル正規表現。既定空欄（全画面）。"""
    _vlm_agent_env_loaded()
    return os.getenv("VISION_VTT_WINDOW", "")


def get_vision_vtt_grid_size() -> int:
    """1グリッドセルのピクセルサイズ。既定 100。"""
    _vlm_agent_env_loaded()
    try:
        return max(1, int(os.getenv("VISION_VTT_GRID_SIZE", "100")))
    except ValueError:
        return 100


def _parse_region_env(key: str) -> tuple[int, int, int, int] | None:
    """'left,top,right,bottom' 形式の環境変数を tuple に変換する。"""
    raw = os.getenv(key, "").strip()
    if not raw:
        return None
    try:
        parts = [int(v) for v in raw.split(",")]
        if len(parts) == 4:
            return (parts[0], parts[1], parts[2], parts[3])
    except ValueError:
        pass
    return None


def get_vision_vtt_chat_region() -> tuple[int, int, int, int] | None:
    """チャットログのキャプチャ領域 (left, top, right, bottom)。省略可。"""
    _vlm_agent_env_loaded()
    return _parse_region_env("VISION_VTT_CHAT_LOG_REGION")


def get_vision_vtt_board_region() -> tuple[int, int, int, int] | None:
    """ボードのキャプチャ領域 (left, top, right, bottom)。省略可。"""
    _vlm_agent_env_loaded()
    return _parse_region_env("VISION_VTT_BOARD_REGION")


def get_vision_vtt_max_steps() -> int:
    """VisionVTT の VLM 操作最大ステップ数。既定 6。"""
    _vlm_agent_env_loaded()
    try:
        return max(1, int(os.getenv("VISION_VTT_MAX_STEPS", "6")))
    except ValueError:
        return 6
