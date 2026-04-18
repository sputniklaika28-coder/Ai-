"""base_adapter.py - VTT操作の抽象インターフェース（非同期API）。

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
    async def connect(self, room_url: str, headless: bool = False,
                      cdp_url: str | None = None,
                      mode: str = "persistent",
                      profile_dir: str | None = None,
                      channel: str | None = None) -> None:
        """VTTルームに接続する。

        Args:
            room_url: VTTルームのURL。
            headless: ヘッドレスモードで起動するか。
            cdp_url: CDP (Chrome DevTools Protocol) エンドポイントURL。
                     指定時は既存ブラウザに接続し、GMの認証・権限を引き継ぐ。
            mode: "persistent" | "fresh" | "cdp"。persistent が既定。
                persistent → 永続プロファイルでログイン状態を保持（CDP不要）
                fresh → 毎回新規ブラウザ起動
                cdp → 既存ブラウザにCDP接続
            profile_dir: persistent モード時のプロファイル保存先。
            channel: "chrome"/"msedge" 等のブラウザチャンネル。
        """

    @abstractmethod
    async def close(self) -> None:
        """ブラウザを閉じて接続を切断する。"""

    @abstractmethod
    async def get_board_state(self) -> list[dict]:
        """ボード上の全駒の位置情報を取得する。"""

    @abstractmethod
    async def move_piece(self, piece_id: str, grid_x: int, grid_y: int) -> bool:
        """駒を指定グリッド座標に移動する。"""

    @abstractmethod
    async def spawn_piece(self, character_json: dict) -> bool:
        """キャラクターデータをVTTに配置する。"""

    @abstractmethod
    async def send_chat(self, character_name: str, text: str) -> bool:
        """チャットメッセージを送信する。"""

    @abstractmethod
    async def get_chat_messages(self) -> list[dict]:
        """チャットメッセージ一覧を取得する。"""

    @abstractmethod
    async def take_screenshot(self) -> bytes | None:
        """画面のスクリーンショットを取得する。"""

    # Phase 2 オプショナルメソッド（非 abstract）

    def upload_asset(self, file_path: str, asset_type: str) -> str | None:
        """ローカルファイルを VTT にアップロードする。

        Args:
            file_path: アップロードするファイルの絶対パス。
            asset_type: アセット種別。

        Returns:
            アップロードされたアセットの URL。未対応の場合 None。
        """
        raise NotImplementedError(
            f"{type(self).__name__} は upload_asset に対応していません"
        )

    def take_canvas_screenshot(self) -> bytes | None:
        """Canvas / ボード領域のみのスクリーンショットを取得する。

        Returns:
            PNG画像のバイト列。未対応の場合 None。
        """
        raise NotImplementedError(
            f"{type(self).__name__} は take_canvas_screenshot に対応していません"
        )
