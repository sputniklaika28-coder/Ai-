"""dependency_checker.py — 依存関係チェッカー。

起動時に不足パッケージを検出し、インストール方法を案内する。
GUI（tkinter）とCLI 両方で利用可能。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Dependency:
    """チェック対象の依存パッケージ。"""

    import_name: str  # import する名前
    pip_name: str  # pip install する名前
    group: str  # "core" / "browser_use" / "dev" / "knowledge"
    description: str  # 日本語説明
    install_hint: str = ""  # 追加のインストール手順


# ──────────────────────────────────────────
# パッケージ定義
# ──────────────────────────────────────────

ALL_DEPENDENCIES: list[Dependency] = [
    # コア
    Dependency("requests", "requests>=2.31", "core", "HTTP通信"),
    Dependency("playwright", "playwright>=1.40", "core", "ブラウザ自動操作",
               "インストール後に: playwright install chromium"),
    # Browser Use 連携
    Dependency("dotenv", "python-dotenv>=1.0", "browser_use", ".env 設定ファイル読み込み"),
    Dependency("browser_use", "browser-use>=0.1.40", "browser_use", "LLM駆動ブラウザ自動化"),
    Dependency("langchain_openai", "langchain-openai>=0.1", "browser_use",
               "Browser Use OpenAI 連携"),
    Dependency("langchain_anthropic", "langchain-anthropic>=0.1", "browser_use",
               "Browser Use Claude 連携"),
    # ナレッジ検索
    Dependency("chromadb", "chromadb>=0.4", "knowledge", "RAG ベクトル検索"),
    Dependency("duckduckgo_search", "duckduckgo-search>=6.0", "knowledge", "Web 検索ツール"),
    # 開発
    Dependency("pytest", "pytest>=8.0", "dev", "テストフレームワーク"),
    Dependency("ruff", "ruff>=0.4", "dev", "リンター"),
]

# ──────────────────────────────────────────
# チェック結果
# ──────────────────────────────────────────


@dataclass
class CheckResult:
    """1パッケージのチェック結果。"""

    dep: Dependency
    installed: bool
    version: str = ""


def check_all() -> list[CheckResult]:
    """全依存パッケージの状態をチェックする。"""
    results: list[CheckResult] = []
    for dep in ALL_DEPENDENCIES:
        try:
            mod = importlib.import_module(dep.import_name)
            ver = getattr(mod, "__version__", "")
            results.append(CheckResult(dep=dep, installed=True, version=ver))
        except ImportError:
            results.append(CheckResult(dep=dep, installed=False))
    return results


def check_group(group: str) -> list[CheckResult]:
    """指定グループの依存パッケージをチェックする。"""
    return [r for r in check_all() if r.dep.group == group]


def get_missing(group: str | None = None) -> list[CheckResult]:
    """未インストールのパッケージを取得する。"""
    results = check_all() if group is None else check_group(group)
    return [r for r in results if not r.installed]


# ──────────────────────────────────────────
# エラーメッセージ生成
# ──────────────────────────────────────────

_GROUP_LABELS = {
    "core": "コア（必須）",
    "browser_use": "Browser Use 連携",
    "knowledge": "ナレッジ検索（RAG）",
    "dev": "開発ツール",
}

_GROUP_INSTALL_CMDS = {
    "core": 'pip install "requests>=2.31" "playwright>=1.40" && playwright install chromium',
    "browser_use": 'pip install ".[browser-use]"',
    "knowledge": 'pip install "chromadb>=0.4" "duckduckgo-search>=6.0"',
    "dev": 'pip install ".[dev]"',
}


def format_missing_report(missing: list[CheckResult] | None = None) -> str:
    """不足パッケージのレポートを人間が読める形式で生成する。"""
    if missing is None:
        missing = get_missing()
    if not missing:
        return "全ての依存パッケージがインストール済みです。"

    lines: list[str] = [
        "=" * 55,
        " 不足している依存パッケージが見つかりました",
        "=" * 55,
        "",
    ]

    # グループ別に整理
    groups: dict[str, list[CheckResult]] = {}
    for r in missing:
        groups.setdefault(r.dep.group, []).append(r)

    for group, results in groups.items():
        label = _GROUP_LABELS.get(group, group)
        lines.append(f"【{label}】")
        for r in results:
            lines.append(f"  ✗ {r.dep.pip_name}  — {r.dep.description}")
            if r.dep.install_hint:
                lines.append(f"    ※ {r.dep.install_hint}")
        lines.append(f"  → インストール: {_GROUP_INSTALL_CMDS.get(group, '')}")
        lines.append("")

    lines.append("-" * 55)
    lines.append("全てまとめてインストール:")
    lines.append('  pip install ".[browser-use,dev]" && playwright install chromium')
    lines.append("")

    return "\n".join(lines)


def print_missing_report() -> bool:
    """不足パッケージがあればレポートを標準出力に表示する。

    Returns:
        不足がなければ True、あれば False。
    """
    missing = get_missing()
    if not missing:
        return True
    print(format_missing_report(missing))
    return False


# ──────────────────────────────────────────
# pip install 実行
# ──────────────────────────────────────────


def install_packages(pip_names: list[str]) -> tuple[bool, str]:
    """pip install を実行する。

    Args:
        pip_names: インストールするパッケージ名リスト。

    Returns:
        (成功, 出力テキスト)
    """
    cmd = [sys.executable, "-m", "pip", "install", *pip_names]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except Exception as e:
        return False, str(e)


def install_group(group: str) -> tuple[bool, str]:
    """グループ単位でインストールする。"""
    missing = get_missing(group)
    if not missing:
        return True, "全てインストール済みです。"
    pip_names = [r.dep.pip_name for r in missing]
    return install_packages(pip_names)


# ──────────────────────────────────────────
# CLI エントリーポイント
# ──────────────────────────────────────────

if __name__ == "__main__":
    print_missing_report()
