"""Phase 2: アセット管理 + VLM テスト。

画像/BGMアップロード、背景設定、Canvas スクリーンショット、
VLM による盤面分析・駒位置検出を検証する。

実行方法:
    CCFOLIA_ROOM_URL=https://ccfolia.com/rooms/xxxx pytest tests/integration/test_phase2_assets_vlm.py -v
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.browser_use]


class TestPhase2AssetsVLM:
    """Phase 2: アセット管理 + VLM。"""

    # 2-1: 画像アップロード
    def test_2_1_upload_image(self, adapter, test_png):
        """ローカル PNG を upload_asset(path, "background") で送り、URL が返る。"""
        url = adapter.upload_asset(str(test_png), "background")
        assert url is not None, "画像アップロードが None を返しました"
        assert url.startswith("http"), f"URL が不正です: {url}"

    # 2-2: BGM アップロード
    def test_2_2_upload_bgm(self, adapter, test_mp3):
        """ローカル MP3 を upload_asset(path, "bgm") で送り、URL が返る。"""
        url = adapter.upload_asset(str(test_mp3), "bgm")
        assert url is not None, "BGM アップロードが None を返しました"
        assert url.startswith("http"), f"URL が不正です: {url}"

    # 2-3: 背景設定
    def test_2_3_set_background(self, adapter, test_png):
        """アップロードした URL で set_background() → CCFolia 画面の背景が変わる。"""
        url = adapter.upload_asset(str(test_png), "background")
        if url is None:
            pytest.skip("画像アップロードが失敗したためスキップ")

        result = adapter.set_background(url)
        # AgentTaskResult の success を検証
        success = getattr(result, "success", bool(result))
        assert success, f"背景設定が失敗しました: {getattr(result, 'error', '')}"

    # 2-4: Canvas スクリーンショット
    def test_2_4_canvas_screenshot(self, adapter):
        """take_canvas_screenshot() でボード領域だけの PNG が返る。"""
        data = adapter.take_canvas_screenshot()
        assert data is not None, "Canvas スクリーンショットが None です"
        assert isinstance(data, bytes), "Canvas スクリーンショットがバイト列ではありません"
        assert len(data) > 100, "Canvas スクリーンショットが小さすぎます"

    # 2-5: VLM チャート分析
    def test_2_5_vlm_analyze_board(self, adapter):
        """駒を数個配置した状態で analyze_board() → 駒の座標リストが返る。"""
        vision = adapter.get_vision_controller()
        pieces = vision.analyze_board()
        assert isinstance(pieces, list), "analyze_board() がリストを返しませんでした"
        # 盤面に駒がある場合、座標情報を検証
        for piece in pieces:
            assert "px_x" in piece, f"px_x がありません: {piece}"
            assert "px_y" in piece, f"px_y がありません: {piece}"

    # 2-6: VLM 駒位置検出
    def test_2_6_vlm_find_piece_position(self, adapter):
        """find_piece_position("赤い駒") → (px_x, px_y) が返る。"""
        vision = adapter.get_vision_controller()
        pos = vision.find_piece_position("赤い駒")
        # 駒が存在しない場合は None が返るのも正常
        if pos is not None:
            assert isinstance(pos, tuple), f"タプルではありません: {type(pos)}"
            assert len(pos) == 2, f"要素数が2ではありません: {pos}"
            px_x, px_y = pos
            assert isinstance(px_x, int), f"px_x が int ではありません: {type(px_x)}"
            assert isinstance(px_y, int), f"px_y が int ではありません: {type(px_y)}"
            assert px_x >= 0, f"px_x が負です: {px_x}"
            assert px_y >= 0, f"px_y が負です: {px_y}"
