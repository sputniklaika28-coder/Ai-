"""asset_uploader.py — CCFolia アセットアップロードモジュール。

Browser Use で CCFolia のアップロード UI に遷移し、
Playwright の set_input_files() でファイルダイアログをバイパスして
画像・音声ファイルを直接注入する。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Browser Use Agent の遅延インポート
try:
    from core.browser_use_agent import BrowserUseAgentWrapper
    from core.vtt_adapters.playwright_utils import find_file_input, set_file_via_input
except ModuleNotFoundError:
    from browser_use_agent import BrowserUseAgentWrapper  # type: ignore[no-redef]
    from vtt_adapters.playwright_utils import (  # type: ignore[no-redef]
        find_file_input,
        set_file_via_input,
    )

# アップロード可能な画像拡張子
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
_AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".m4a", ".aac"}


class AssetUploader:
    """ローカルファイルを CCFolia にアップロードする。

    Browser Use でアップロード UI に遷移し、
    Playwright の set_input_files() でファイルダイアログをバイパスする。
    アップロード済みファイルはレジストリ（ファイル名 → URL）で管理する。
    """

    def __init__(self, page: object, agent: BrowserUseAgentWrapper | None = None) -> None:
        self._page = page
        self._agent = agent
        self._registry: dict[str, str] = {}

    @property
    def registry(self) -> dict[str, str]:
        """アップロード済みアセットのレジストリ（ファイル名 → URL）。"""
        return dict(self._registry)

    # ──────────────────────────────────────────
    # 公開 API
    # ──────────────────────────────────────────

    def upload_image(
        self, file_path: str, asset_type: str = "background"
    ) -> str | None:
        """画像ファイルを CCFolia にアップロードする。

        Args:
            file_path: ローカルの画像ファイルパス。
            asset_type: アセット種別（"background", "token" 等）。

        Returns:
            アップロードされた画像の URL。失敗時は None。
        """
        path = Path(file_path)
        if not path.exists():
            logger.error("ファイルが見つかりません: %s", file_path)
            return None
        if path.suffix.lower() not in _IMAGE_EXTS:
            logger.error("サポートされない画像形式: %s", path.suffix)
            return None

        if not self._navigate_to_upload_ui(asset_type):
            return None
        if not self._inject_file(file_path):
            return None
        url = self._extract_uploaded_url()
        self._confirm_and_close()

        if url:
            self._registry[path.name] = url
            logger.info("画像アップロード完了: %s → %s", path.name, url[:60])
        return url

    def upload_audio(self, file_path: str, bgm_name: str = "") -> str | None:
        """音声ファイルを CCFolia の BGM としてアップロードする。

        Args:
            file_path: ローカルの音声ファイルパス。
            bgm_name: BGM 名（空の場合はファイル名を使用）。

        Returns:
            アップロードされた音声の URL。失敗時は None。
        """
        path = Path(file_path)
        if not path.exists():
            logger.error("ファイルが見つかりません: %s", file_path)
            return None
        if path.suffix.lower() not in _AUDIO_EXTS:
            logger.error("サポートされない音声形式: %s", path.suffix)
            return None

        name = bgm_name or path.stem
        if not self._navigate_to_audio_upload_ui():
            return None
        if not self._inject_file(file_path):
            return None
        url = self._extract_uploaded_url()
        self._confirm_and_close()

        if url:
            self._registry[name] = url
            logger.info("BGMアップロード完了: %s → %s", name, url[:60])
        return url

    def upload_batch(
        self, folder_path: str, asset_type: str = "background"
    ) -> dict[str, str]:
        """フォルダ内の全ファイルをバッチアップロードする。

        Args:
            folder_path: アップロード対象フォルダのパス。
            asset_type: アセット種別。

        Returns:
            {ファイル名: URL} の辞書。
        """
        folder = Path(folder_path)
        if not folder.is_dir():
            logger.error("フォルダが見つかりません: %s", folder_path)
            return {}

        results: dict[str, str] = {}
        all_exts = _IMAGE_EXTS | _AUDIO_EXTS
        files = sorted(f for f in folder.iterdir() if f.suffix.lower() in all_exts)

        for f in files:
            if f.suffix.lower() in _IMAGE_EXTS:
                url = self.upload_image(str(f), asset_type)
            else:
                url = self.upload_audio(str(f))
            if url:
                results[f.name] = url

        logger.info("バッチアップロード完了: %d/%d 成功", len(results), len(files))
        return results

    def get_asset_url(self, name: str) -> str | None:
        """レジストリからアセット URL を取得する。"""
        return self._registry.get(name)

    # ──────────────────────────────────────────
    # 内部メソッド
    # ──────────────────────────────────────────

    def _navigate_to_upload_ui(self, asset_type: str) -> bool:
        """Browser Use で画像アップロード UI に遷移する。"""
        if self._agent is None:
            logger.warning("Browser Use Agent 未設定。UI遷移をスキップします。")
            return True
        try:
            task = self._agent.format_task(
                "navigate_to_image_upload", asset_type=asset_type,
            )
            result = self._agent.run_task_sync(task)
            return result.success
        except Exception as e:
            logger.error("画像アップロードUI遷移エラー: %s", e)
            return False

    def _navigate_to_audio_upload_ui(self) -> bool:
        """Browser Use で BGM アップロード UI に遷移する。"""
        if self._agent is None:
            logger.warning("Browser Use Agent 未設定。UI遷移をスキップします。")
            return True
        try:
            task = self._agent.format_task("navigate_to_audio_upload")
            result = self._agent.run_task_sync(task)
            return result.success
        except Exception as e:
            logger.error("BGMアップロードUI遷移エラー: %s", e)
            return False

    def _inject_file(self, file_path: str) -> bool:
        """Playwright の set_input_files でファイルを注入する。"""
        el = find_file_input(self._page)
        if el is None:
            logger.error("input[type='file'] が見つかりません")
            return False
        return set_file_via_input(self._page, file_path)

    def _extract_uploaded_url(self) -> str | None:
        """アップロード完了後、DOM から新しいアセット URL を取得する。"""
        try:
            self._page.wait_for_timeout(1000)  # type: ignore[union-attr]
            # Firebase Storage URL または CCFolia のアセット URL を検索
            url = self._page.evaluate("""() => {  // type: ignore[union-attr]
                // 最新のアップロード画像URLを探す
                const imgs = document.querySelectorAll('img[src*="firebasestorage"], img[src*="ccfolia"]');
                if (imgs.length > 0) return imgs[imgs.length - 1].src;
                // input の value にURLが入っている場合
                const inputs = document.querySelectorAll('input[value*="http"]');
                if (inputs.length > 0) return inputs[inputs.length - 1].value;
                return null;
            }""")
            return url
        except Exception as e:
            logger.error("URL抽出エラー: %s", e)
            return None

    def _confirm_and_close(self) -> bool:
        """アップロード完了後、設定パネルを閉じる。"""
        if self._agent is None:
            return True
        try:
            task = self._agent.format_task("confirm_upload")
            self._agent.run_task_sync(task, max_steps=5)
            task = self._agent.format_task("close_settings")
            self._agent.run_task_sync(task, max_steps=5)
            return True
        except Exception as e:
            logger.error("確認・クローズエラー: %s", e)
            return False
