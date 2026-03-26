"""base_adapter.py — VTT操作の抽象インターフェース（同期API）。

すべてのVTTアダプター（CCFolia、ユドナリウム等）はこのクラスを継承し、
統一されたインターフェースでブラウザ操作を提供する。
"""

from abc import ABC, abstractmethod


class BaseVTTAdapter(ABC):
    """VTT操作の抽象基底クラス。

    制御層（CCFoliaConnector）はこのインターフェースを通じて
    VTTプラットフォームに依存しない形でブラウザ操作を行う。
    """

    @abstractmethod
    def connect(self, room_url: str, headless: bool = False) -> None:
        """VTTルームに接続する。

        Args:
            room_url: VTTルームのURL。
            headless: ヘッドレスモードで起動するかどうか。
        """

    @abstractmethod
    def close(self) -> None:
        """ブラウザを閉じて接続を切断する。"""

    @abstractmethod
    def get_board_state(self) -> list[dict]:
        """ボード上の全駒の位置情報を取得する。

        Returns:
            駒情報のリスト。各駒は以下のキーを含む:
            - index: int — DOM上のインデックス
            - img_hash: str — 画像の8文字ハッシュ
            - img_url: str — 画像URL
            - px_x, px_y: int — ピクセル座標
            - grid_x, grid_y: int — グリッド座標
        """

    @abstractmethod
    def move_piece(self, piece_id: str, grid_x: int, grid_y: int) -> bool:
        """駒を指定グリッド座標に移動する。

        Args:
            piece_id: 駒の識別子（img_hash等）。
            grid_x: 移動先のグリッドX座標。
            grid_y: 移動先のグリッドY座標。

        Returns:
            移動が成功した場合 True。
        """

    @abstractmethod
    def spawn_piece(self, character_json: dict) -> bool:
        """キャラクターデータをVTTに配置する。

        Args:
            character_json: VTTプラットフォーム形式のキャラクターデータ。

        Returns:
            配置が成功した場合 True。
        """

    @abstractmethod
    def send_chat(self, character_name: str, text: str) -> bool:
        """チャットメッセージを送信する。

        Args:
            character_name: 発言キャラクター名。
            text: 送信するテキスト。

        Returns:
            送信が成功した場合 True。
        """

    @abstractmethod
    def get_chat_messages(self) -> list[dict]:
        """チャットメッセージ一覧を取得する。

        Returns:
            メッセージのリスト。各メッセージは以下のキーを含む:
            - speaker: str — 発言者名
            - body: str — メッセージ本文
        """

    @abstractmethod
    def take_screenshot(self) -> bytes | None:
        """画面のスクリーンショットを取得する。

        Returns:
            PNG画像のバイト列。取得できない場合 None。
        """
