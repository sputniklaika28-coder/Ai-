"""phase1_validation.py — Browser Use Phase 1 PoC 検証スクリプト。

Browser Use が CCFolia に対して基本的な DOM 操作を
自律的に実行できるかを検証する。

使用方法:
    python scripts/phase1_validation.py --room-url https://ccfolia.com/rooms/xxxx

前提条件:
    - configs/.env に OPENAI_API_KEY または ANTHROPIC_API_KEY を設定済み
    - pip install 'tactical-exorcist-trpg-ai[browser-use]' を実行済み
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# リポジトリルートをパスに追加
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core"))
sys.path.insert(0, str(_ROOT))


def _timer(func, *args, **kwargs):
    """関数の実行時間を計測する。"""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return result, elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser Use Phase 1 PoC 検証")
    parser.add_argument(
        "--room-url",
        required=True,
        help="CCFolia ルームの URL",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="ヘッドレスモードで実行",
    )
    args = parser.parse_args()

    # 設定読み込み
    from core.config import load_config

    cfg = load_config()
    api_key = cfg["openai_api_key"] or cfg["anthropic_api_key"]
    if not api_key:
        print("❌ configs/.env に OPENAI_API_KEY または ANTHROPIC_API_KEY を設定してください")
        print("   手順:")
        print("   1. configs/.env をテキストエディタで開く")
        print("   2. OPENAI_API_KEY=sk-... の行に実際のキーを入力")
        print("      または ANTHROPIC_API_KEY=sk-ant-... を設定")
        print("   3. このスクリプトを再実行")
        sys.exit(1)

    provider = "anthropic" if cfg["anthropic_api_key"] and not cfg["openai_api_key"] else "openai"
    model = cfg["browser_use_model"]
    print(f"📋 設定: model={model}, provider={provider}")

    from core.browser_use_agent import BrowserUseAgentWrapper

    agent = BrowserUseAgentWrapper(
        model_name=model,
        api_key=api_key,
        provider=provider,
        headless=args.headless,
    )

    results = []

    # ──────────────────────────────────────────
    # Task 1: ルームに接続
    # ──────────────────────────────────────────
    print("\n" + "=" * 50)
    print("Task 1: ルーム接続")
    print("=" * 50)
    result, elapsed = _timer(
        agent.run_task_sync,
        f"ブラウザで以下のURLにアクセスしてください: {args.room_url}\n"
        "ページが完全に読み込まれるまで待ってください。",
    )
    status = "✅" if result.success else "❌"
    print(f"  {status} 結果: success={result.success} ({elapsed:.1f}秒)")
    if result.error:
        print(f"  エラー: {result.error}")
    results.append(("ルーム接続", result.success, elapsed))

    if not result.success:
        print("\n❌ ルーム接続に失敗したため、以降のタスクをスキップします。")
        agent.close_sync()
        sys.exit(1)

    # ──────────────────────────────────────────
    # Task 2: チャット送信
    # ──────────────────────────────────────────
    print("\n" + "=" * 50)
    print("Task 2: チャット送信")
    print("=" * 50)
    task = agent.format_task(
        "send_chat",
        character_name="テストAI",
        text="[AI] Phase 1 PoC 検証メッセージ",
    )
    result, elapsed = _timer(agent.run_task_sync, task)
    status = "✅" if result.success else "❌"
    print(f"  {status} 結果: success={result.success} ({elapsed:.1f}秒)")
    if result.error:
        print(f"  エラー: {result.error}")
    results.append(("チャット送信", result.success, elapsed))

    # ──────────────────────────────────────────
    # Task 3: キャラクターパラメータ設定
    # ──────────────────────────────────────────
    print("\n" + "=" * 50)
    print("Task 3: キャラクターパラメータ設定")
    print("=" * 50)
    task = agent.format_task(
        "set_character_params",
        name="テストNPC",
        hp="50",
        initiative="10",
    )
    result, elapsed = _timer(agent.run_task_sync, task)
    status = "✅" if result.success else "❌"
    print(f"  {status} 結果: success={result.success} ({elapsed:.1f}秒)")
    if result.error:
        print(f"  エラー: {result.error}")
    results.append(("キャラクター設定", result.success, elapsed))

    # ──────────────────────────────────────────
    # サマリー
    # ──────────────────────────────────────────
    print("\n" + "=" * 50)
    print("検証サマリー")
    print("=" * 50)
    total = len(results)
    passed = sum(1 for _, s, _ in results if s)
    for name, success, elapsed in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} | {name} | {elapsed:.1f}秒")
    print(f"\n合計: {passed}/{total} 成功")

    agent.close_sync()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
