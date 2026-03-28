"""
test_asset_uploader.py — AssetUploader のユニットテスト

Mock Page + Mock Agent を使用し、ファイル検証・
set_input_files 呼び出し・レジストリ管理をテストする。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.asset_uploader import _AUDIO_EXTS, _IMAGE_EXTS, AssetUploader

# ──────────────────────────────────────────
# フィクスチャ
# ──────────────────────────────────────────


@pytest.fixture()
def mock_page():
    page = MagicMock()
    page.query_selector.return_value = MagicMock()  # input[type="file"]
    page.set_input_files.return_value = None
    page.evaluate.return_value = "https://firebasestorage.example.com/img123"
    page.wait_for_timeout.return_value = None
    return page


@pytest.fixture()
def mock_agent():
    agent = MagicMock()
    result = MagicMock()
    result.success = True
    agent.run_task_sync.return_value = result
    agent.format_task.return_value = "dummy task"
    return agent


@pytest.fixture()
def uploader(mock_page, mock_agent):
    return AssetUploader(page=mock_page, agent=mock_agent)


@pytest.fixture()
def uploader_no_agent(mock_page):
    return AssetUploader(page=mock_page, agent=None)


# ──────────────────────────────────────────
# 画像アップロード
# ──────────────────────────────────────────


class TestUploadImage:
    def test_rejects_missing_file(self, uploader):
        result = uploader.upload_image("/nonexistent/missing.png")
        assert result is None

    def test_rejects_unsupported_extension(self, uploader, tmp_path):
        bad_file = tmp_path / "test.bmp"
        bad_file.write_bytes(b"\x00")
        result = uploader.upload_image(str(bad_file))
        assert result is None

    def test_upload_image_success(self, uploader, mock_page, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG")

        url = uploader.upload_image(str(img), asset_type="background")

        assert url == "https://firebasestorage.example.com/img123"
        assert uploader.registry["test.png"] == url

    def test_upload_image_without_agent(self, uploader_no_agent, mock_page, tmp_path):
        """Agent 無しでも set_input_files 経由でアップロードできる。"""
        img = tmp_path / "icon.jpg"
        img.write_bytes(b"\xFF\xD8")

        url = uploader_no_agent.upload_image(str(img))
        assert url is not None

    def test_supported_image_extensions(self):
        assert ".png" in _IMAGE_EXTS
        assert ".jpg" in _IMAGE_EXTS
        assert ".webp" in _IMAGE_EXTS
        assert ".svg" in _IMAGE_EXTS


# ──────────────────────────────────────────
# 音声アップロード
# ──────────────────────────────────────────


class TestUploadAudio:
    def test_rejects_missing_file(self, uploader):
        result = uploader.upload_audio("/nonexistent/missing.mp3")
        assert result is None

    def test_rejects_unsupported_extension(self, uploader, tmp_path):
        bad_file = tmp_path / "test.flac"
        bad_file.write_bytes(b"\x00")
        result = uploader.upload_audio(str(bad_file))
        assert result is None

    def test_upload_audio_success(self, uploader, tmp_path):
        audio = tmp_path / "bgm.mp3"
        audio.write_bytes(b"\xFF\xFB")

        url = uploader.upload_audio(str(audio), bgm_name="battle_bgm")

        assert url == "https://firebasestorage.example.com/img123"
        assert uploader.registry["battle_bgm"] == url

    def test_upload_audio_uses_stem_when_no_name(self, uploader, tmp_path):
        audio = tmp_path / "ambient.ogg"
        audio.write_bytes(b"OggS")

        uploader.upload_audio(str(audio))
        assert "ambient" in uploader.registry

    def test_supported_audio_extensions(self):
        assert ".mp3" in _AUDIO_EXTS
        assert ".ogg" in _AUDIO_EXTS
        assert ".m4a" in _AUDIO_EXTS


# ──────────────────────────────────────────
# バッチアップロード
# ──────────────────────────────────────────


class TestUploadBatch:
    def test_batch_upload_filters_by_extension(self, uploader, tmp_path):
        (tmp_path / "bg.png").write_bytes(b"\x89PNG")
        (tmp_path / "readme.txt").write_text("ignore me")
        (tmp_path / "bgm.mp3").write_bytes(b"\xFF\xFB")

        results = uploader.upload_batch(str(tmp_path))

        # png と mp3 の2ファイルがアップロードされる
        assert len(results) == 2
        assert "bg.png" in results
        assert "bgm.mp3" in results

    def test_batch_upload_nonexistent_folder(self, uploader):
        results = uploader.upload_batch("/nonexistent/folder")
        assert results == {}


# ──────────────────────────────────────────
# レジストリ
# ──────────────────────────────────────────


class TestRegistry:
    def test_get_asset_url(self, uploader, tmp_path):
        img = tmp_path / "token.png"
        img.write_bytes(b"\x89PNG")
        uploader.upload_image(str(img))

        assert uploader.get_asset_url("token.png") is not None

    def test_get_asset_url_missing(self, uploader):
        assert uploader.get_asset_url("nonexistent.png") is None

    def test_registry_is_copy(self, uploader):
        """registry プロパティは内部辞書のコピーを返す。"""
        reg = uploader.registry
        reg["hacked"] = "value"
        assert "hacked" not in uploader.registry


# ──────────────────────────────────────────
# 内部メソッド
# ──────────────────────────────────────────


class TestInternalMethods:
    def test_inject_file_returns_false_when_no_input(self, uploader, mock_page):
        mock_page.query_selector.return_value = None
        result = uploader._inject_file("/some/file.png")
        assert result is False

    def test_navigate_failure_aborts_upload(self, uploader, mock_agent, tmp_path):
        img = tmp_path / "fail.png"
        img.write_bytes(b"\x89PNG")

        mock_agent.run_task_sync.return_value.success = False
        result = uploader.upload_image(str(img))
        assert result is None

    def test_extract_url_returns_none_on_error(self, uploader, mock_page):
        mock_page.wait_for_timeout.side_effect = Exception("timeout")
        result = uploader._extract_uploaded_url()
        assert result is None
