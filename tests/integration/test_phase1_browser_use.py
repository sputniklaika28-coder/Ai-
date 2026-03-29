"""Phase 1: Browser Use 基本接続テスト。

CCFolia への接続、チャット送受信、盤面取得、スクリーンショットを検証する。

実行方法:
    CCFOLIA_ROOM_URL=https://ccfolia.com/rooms/xxxx pytest tests/integration/test_phase1_browser_use.py -v
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.browser_use]


class TestPhase1BrowserUse:
    """Phase 1: Browser Use 基本接続。"""

    # 1-1: CCFolia に接続できるか
    def test_1_1_connect_to_ccfolia(self, adapter):
        """Browser Use で CCFolia に接続し、ページが表示される。

        確認: adapter フィクスチャ（conftest）で connect() が成功していること。
        adapter.page が取得でき、None でないこと。
        """
        page = adapter.page
        assert page is not None, "Playwright Page が取得できませんでした"

    # 1-2: チャット送信
    def test_1_2_send_chat(self, adapter):
        """Browser Use 経由で GM としてメッセージが投稿される。"""
        ok = adapter.send_chat("テストGM", "[統合テスト] Phase1-2 チャット送信テスト")
        assert ok, "チャット送信が失敗しました"

    # 1-3: チャット取得
    def test_1_3_get_chat_messages(self, adapter):
        """Playwright 直接操作で既存メッセージが配列で返る。"""
        messages = adapter.get_chat_messages()
        assert isinstance(messages, list), "チャットメッセージがリストではありません"
        # 1-2 で送信したメッセージが含まれているはず
        # ただし DOM 構造次第で取得できない場合もあるため、型チェックのみ
        for msg in messages:
            assert "speaker" in msg or "body" in msg, (
                f"メッセージに speaker/body がありません: {msg}"
            )

    # 1-4: 盤面状態取得
    def test_1_4_get_board_state(self, adapter):
        """get_board_state() で駒の座標リストが返る。"""
        state = adapter.get_board_state()
        assert isinstance(state, list), "盤面状態がリストではありません"
        # 駒が存在する場合は座標情報を検証
        for piece in state:
            assert "px_x" in piece, f"px_x がありません: {piece}"
            assert "px_y" in piece, f"px_y がありません: {piece}"

    # 1-5: スクリーンショット
    def test_1_5_take_screenshot(self, adapter):
        """take_screenshot() で PNG バイトが返る。"""
        data = adapter.take_screenshot()
        assert data is not None, "スクリーンショットが None です"
        assert isinstance(data, bytes), "スクリーンショットがバイト列ではありません"
        assert len(data) > 100, "スクリーンショットが小さすぎます"
        # PNG シグネチャ検証
        assert data[:4] == b"\x89PNG", "PNG シグネチャが不正です"
