"""browser_use_agent.py — Browser Use エージェントのラッパー。

Browser Use の async Agent を初期化し、自然言語タスクを
VTT の DOM 操作に変換する。既存の同期コードベースとの互換性のため
asyncio.run() で同期ブリッジを提供する。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Browser Use / LangChain の遅延インポート（optional dependency）
_HAS_BROWSER_USE = False
try:
    from browser_use import Agent, BrowserSession
    from browser_use.browser import BrowserProfile
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI

    _HAS_BROWSER_USE = True
except ModuleNotFoundError:
    Agent = BrowserSession = BrowserProfile = None  # type: ignore[assignment,misc]
    ChatOpenAI = ChatAnthropic = None  # type: ignore[assignment,misc]

_TASKS_PATH = Path(__file__).resolve().parent.parent / "configs" / "browser_use_tasks.json"


@dataclass
class AgentTaskResult:
    """Browser Use タスクの実行結果。"""

    success: bool
    output: str = ""
    error: str = ""
    steps: int = 0
    extra: dict = field(default_factory=dict)


def _load_task_templates() -> dict[str, str]:
    """configs/browser_use_tasks.json からタスクテンプレートを読み込む。"""
    if _TASKS_PATH.exists():
        with open(_TASKS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


class BrowserUseAgentWrapper:
    """Browser Use の async Agent を同期APIでラップ。

    Browser Use は内部で Playwright を使用するため、
    既存の CCFoliaAdapter と同じブラウザインスタンスを共有できる。
    ハイブリッド操作では get_playwright_page() で内部 Page を取得し、
    直接 Playwright API を呼び出す。
    """

    def __init__(
        self,
        model_name: str = "",
        api_key: str = "",
        provider: str = "local",
        headless: bool = False,
        lm_studio_url: str = "http://localhost:1234",
    ) -> None:
        if not _HAS_BROWSER_USE:
            raise ModuleNotFoundError(
                "browser-use パッケージが見つかりません。\n"
                "  pip install 'tactical-exorcist-trpg-ai[browser-use]'\n"
                "を実行してください。"
            )

        self._headless = headless
        self._task_templates = _load_task_templates()

        # LLM の初期化
        if provider == "anthropic":
            self._llm = ChatAnthropic(model=model_name or "claude-sonnet-4-20250514", api_key=api_key)  # type: ignore[arg-type]
        elif provider == "local":
            # LM Studio 等の OpenAI 互換ローカルサーバーに接続
            self._llm = ChatOpenAI(
                model=model_name or "",  # type: ignore[arg-type]
                api_key="lm-studio",
                base_url=f"{lm_studio_url}/v1",
            )
        else:
            self._llm = ChatOpenAI(model=model_name or "gpt-4o", api_key=api_key)  # type: ignore[arg-type]

        # Browser Use のプロファイル（再利用可能な設定テンプレート）
        self._browser_profile = BrowserProfile(
            headless=headless,
        )
        self._browser_session: BrowserSession | None = None  # type: ignore[assignment]

    async def _ensure_session(self) -> BrowserSession:  # type: ignore[return]
        """BrowserSession を初期化（遅延）。"""
        if self._browser_session is None:
            self._browser_session = BrowserSession(browser_profile=self._browser_profile)
            await self._browser_session.start()
        return self._browser_session

    async def _run_task(
        self, task: str, url: str | None = None, max_steps: int = 15
    ) -> AgentTaskResult:
        """Browser Use Agent にタスクを実行させる。

        Args:
            task: 自然言語のタスク記述。
            url: タスク実行前にナビゲートするURL（省略可）。
            max_steps: エージェントの最大ステップ数。

        Returns:
            タスク実行結果。
        """
        session = await self._ensure_session()

        # URL 指定があれば事前ナビゲーション
        if url is not None:
            try:
                page = await session.get_current_page()
                await page.goto(url)
            except Exception as e:
                logger.warning("事前ナビゲーション失敗 (url=%s): %s", url, e)

        agent = Agent(
            task=task,
            llm=self._llm,
            browser_session=session,
            max_actions_per_step=5,
        )

        try:
            result = await agent.run(max_steps=max_steps)

            # AgentHistoryList API: history はプロパティ／is_done・final_result はメソッド
            history = getattr(result, "history", None)
            steps = len(history) if history is not None else 0
            is_done_attr = getattr(result, "is_done", None)
            success = bool(is_done_attr() if callable(is_done_attr) else is_done_attr)
            final_attr = getattr(result, "final_result", None)
            output = (final_attr() if callable(final_attr) else final_attr) or ""

            return AgentTaskResult(
                success=success,
                output=output,
                steps=steps,
            )
        except Exception as e:
            logger.error("Browser Use タスク失敗: %s", e)
            return AgentTaskResult(success=False, error=str(e))

    def run_task_sync(
        self, task: str, url: str | None = None, max_steps: int = 15
    ) -> AgentTaskResult:
        """同期版タスク実行（既存コードとの互換性のため）。

        Args:
            task: 自然言語のタスク記述。
            url: タスク実行前にナビゲートするURL（省略可）。
            max_steps: エージェントの最大ステップ数。

        Returns:
            タスク実行結果。
        """
        return asyncio.run(self._run_task(task, url=url, max_steps=max_steps))

    def format_task(self, template_name: str, **kwargs: str) -> str:
        """タスクテンプレートにパラメータを埋め込む。

        Args:
            template_name: browser_use_tasks.json のキー名。
            **kwargs: テンプレートに埋め込むパラメータ。

        Returns:
            フォーマット済みのタスク文字列。

        Raises:
            KeyError: テンプレートが見つからない場合。
        """
        template = self._task_templates.get(template_name)
        if template is None:
            raise KeyError(f"タスクテンプレートが見つかりません: {template_name}")
        return template.format(**kwargs)

    async def get_playwright_page(self) -> object | None:
        """Browser Use 内部の Playwright Page を取得（ハイブリッド操作用）。

        Browser Use が管理するセッションの現在のページを返す。
        Canvas 操作や高速ポーリングなど、直接 Playwright API が
        必要な場面で使用する。

        Returns:
            Playwright Page オブジェクト。未初期化の場合 None。
        """
        if self._browser_session is None:
            return None
        try:
            return await self._browser_session.get_current_page()
        except Exception as e:
            logger.warning("Playwright Page の取得に失敗: %s", e)
            return None

    def get_playwright_page_sync(self) -> object | None:
        """同期版 Playwright Page 取得。"""
        return asyncio.run(self.get_playwright_page())

    async def close(self) -> None:
        """ブラウザセッションを閉じてリソースを解放する。"""
        if self._browser_session is not None:
            try:
                await self._browser_session.close()
            except Exception as e:
                logger.warning("BrowserSession のクローズ中に例外: %s", e)
            self._browser_session = None

    def close_sync(self) -> None:
        """同期版クローズ。"""
        asyncio.run(self.close())
