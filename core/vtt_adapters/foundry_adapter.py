"""foundry_adapter.py — Foundry VTT REST API アダプター。

Playwright・ブラウザ不要。`foundryvtt-rest-api` コミュニティモジュールが
Foundry VTT サーバーに導入済みであることが前提。

エンドポイント仕様は foundryvtt-rest-api v1.x に基づく。
    https://github.com/Unarekin/FoundryVTT-Rest-API

設定:
    FOUNDRY_URL      = http://localhost:30000  (Foundry サーバー URL)
    FOUNDRY_API_KEY  = <API キー>
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

try:
    from core.vtt_adapters.base_adapter import BaseVTTAdapter
except ModuleNotFoundError:
    from vtt_adapters.base_adapter import BaseVTTAdapter  # type: ignore

logger = logging.getLogger(__name__)

# Foundry チャットメッセージタイプ定数
_CHAT_TYPE_CHAT = 0
_CHAT_TYPE_ROLL = 5


class FoundryConnectionError(ConnectionError):
    """Foundry VTT サーバーへの接続失敗。"""


class FoundryVTTAdapter(BaseVTTAdapter):
    """Foundry VTT REST API アダプター（HTTP のみ、ブラウザ不要）。

    CCFolia Playwright アダプターの代替として使用可能。
    `foundryvtt-rest-api` モジュールが Foundry に導入済みであること。

    使用例::
        adapter = FoundryVTTAdapter(
            base_url="http://localhost:30000",
            api_key="your-api-key",
        )
        await adapter.connect(room_url="")  # room_url は使用しない
        messages = await adapter.get_chat_messages()
    """

    DEFAULT_GRID_SIZE: int = 100  # Foundry デフォルトのグリッドセルサイズ（px）

    def __init__(
        self,
        base_url: str = "http://localhost:30000",
        api_key: str = "",
        grid_size: int = DEFAULT_GRID_SIZE,
        timeout: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._grid_size = grid_size
        self._timeout = timeout
        self._headers: dict[str, str] = {}
        self._connected: bool = False
        self._active_scene_id: str | None = None

    # ──────────────────────────────────────
    # 内部 HTTP ヘルパー
    # ──────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-Api-Key"] = self._api_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> Any | None:
        """共通 HTTP リクエストヘルパー。JSON レスポンスまたは None を返す。"""
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=self._headers,
                    json=body,
                )
            if resp.status_code in (200, 201):
                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    return resp.json()
                # PNG などバイナリレスポンス
                return resp.content
            logger.warning(
                "FoundryVTT %s %s → HTTP %d: %s",
                method,
                path,
                resp.status_code,
                resp.text[:200],
            )
            return None
        except httpx.TimeoutException:
            logger.error("FoundryVTT %s %s タイムアウト (%ds)", method, path, self._timeout)
            return None
        except Exception as e:
            logger.error("FoundryVTT %s %s エラー: %s", method, path, e)
            return None

    async def _get(self, path: str) -> Any | None:
        return await self._request("GET", path)

    async def _post(self, path: str, body: dict) -> Any | None:
        return await self._request("POST", path, body)

    async def _patch(self, path: str, body: dict) -> Any | None:
        return await self._request("PATCH", path, body)

    # ──────────────────────────────────────
    # データ変換ヘルパー
    # ──────────────────────────────────────

    def _token_to_piece(self, token: dict) -> dict:
        """Foundry トークン dict → 共通 piece dict に変換する。"""
        px_x = int(token.get("x", 0))
        px_y = int(token.get("y", 0))
        return {
            "piece_id": token.get("_id", ""),
            "name": token.get("name", ""),
            "img_url": token.get("img", ""),
            "img_hash": "",
            "px_x": px_x,
            "px_y": px_y,
            "grid_x": px_x // self._grid_size,
            "grid_y": px_y // self._grid_size,
        }

    def _ccfolia_to_foundry_token(self, character_json: dict) -> dict:
        """CCFolia キャラ JSON → Foundry トークン JSON に変換する。

        CCFolia 形式の必須キー:
            name, image.url, params.hp, params.maxHp
        """
        params = character_json.get("params", {})
        image = character_json.get("image", {})
        position = character_json.get("position", {})

        return {
            "name": character_json.get("name", "Unknown"),
            "img": image.get("url", "icons/svg/mystery-man.svg"),
            "width": character_json.get("width", 1),
            "height": character_json.get("height", 1),
            "x": position.get("x", self._grid_size * 5),
            "y": position.get("y", self._grid_size * 5),
            "displayName": 30,   # HOVER_OWNER
            "displayBars": 30,
            "bar1": {"attribute": "attributes.hp"},
            "actorData": {
                "data": {
                    "attributes": {
                        "hp": {
                            "value": params.get("hp", 0),
                            "min": 0,
                            "max": params.get("maxHp", params.get("hp", 0)),
                        }
                    }
                }
            },
        }

    # ──────────────────────────────────────
    # BaseVTTAdapter 実装
    # ──────────────────────────────────────

    async def connect(
        self,
        room_url: str,
        headless: bool = False,
        cdp_url: str | None = None,
    ) -> None:
        """Foundry サーバーへの接続を確認し、アクティブシーン ID を取得する。

        Args:
            room_url: 未使用（HTTP アダプターのため無視）。
            headless: 未使用。
            cdp_url: 未使用。

        Raises:
            FoundryConnectionError: サーバーに到達できない場合。
        """
        self._headers = self._build_headers()

        # 疎通確認
        status = await self._get("/api/status")
        if status is None:
            raise FoundryConnectionError(
                f"Foundry VTT に接続できません: {self._base_url}\n"
                "FOUNDRY_URL と foundryvtt-rest-api モジュールの設定を確認してください。"
            )

        # アクティブシーン取得
        scenes = await self._get("/api/scenes")
        if isinstance(scenes, list):
            active = next((s for s in scenes if s.get("active")), None)
            if active:
                self._active_scene_id = active.get("_id")
                logger.info(
                    "FoundryVTT 接続完了: %s (シーン: %s / %s)",
                    self._base_url,
                    active.get("name", "unknown"),
                    self._active_scene_id,
                )
            else:
                logger.warning("FoundryVTT: アクティブシーンが見つかりません")

        self._connected = True

    async def close(self) -> None:
        """HTTP アダプターはステートレスのため no-op。内部状態をクリアする。"""
        self._connected = False
        self._active_scene_id = None
        logger.info("FoundryVTTAdapter: 接続クローズ")

    async def get_board_state(self) -> list[dict]:
        """アクティブシーンのトークン一覧を取得する。

        Returns:
            piece dict のリスト。各 dict は CCFoliaAdapter と同じキー構造。
        """
        if self._active_scene_id:
            path = f"/api/scenes/{self._active_scene_id}/tokens"
        else:
            path = "/api/tokens"

        raw = await self._get(path)
        if not isinstance(raw, list):
            logger.warning("get_board_state: 想定外のレスポンス型 %s", type(raw))
            return []

        return [self._token_to_piece(t) for t in raw]

    async def move_piece(self, piece_id: str, grid_x: int, grid_y: int) -> bool:
        """トークンをグリッド座標に移動する。

        Args:
            piece_id: Foundry トークンの `_id` 文字列。
            grid_x: 移動先グリッド X 座標。
            grid_y: 移動先グリッド Y 座標。
        """
        if not piece_id:
            logger.warning("move_piece: piece_id が空です")
            return False

        body = {
            "x": grid_x * self._grid_size,
            "y": grid_y * self._grid_size,
        }
        result = await self._patch(f"/api/tokens/{piece_id}", body)
        if result is None:
            return False

        logger.info("move_piece: %s → grid(%d, %d)", piece_id, grid_x, grid_y)
        return True

    async def spawn_piece(self, character_json: dict) -> bool:
        """キャラクター JSON をボードにトークンとして配置する。

        Args:
            character_json: CCFolia 形式のキャラクター dict。
        """
        foundry_token = self._ccfolia_to_foundry_token(character_json)

        # シーン指定 POST
        if self._active_scene_id:
            path = f"/api/scenes/{self._active_scene_id}/tokens"
        else:
            path = "/api/tokens"

        result = await self._post(path, foundry_token)
        if result is None:
            return False

        name = character_json.get("name", "unknown")
        logger.info("spawn_piece: '%s' をボードに配置しました", name)
        return True

    async def send_chat(self, character_name: str, text: str) -> bool:
        """チャットメッセージを投稿する。

        Args:
            character_name: 発言者のキャラクター名。
            text: 投稿テキスト。
        """
        body = {
            "speaker": {"alias": character_name},
            "content": text,
            "type": _CHAT_TYPE_CHAT,
        }
        result = await self._post("/api/messages", body)
        if result is None:
            return False

        logger.info("send_chat: [%s] %s", character_name, text[:50])
        return True

    async def get_chat_messages(self) -> list[dict]:
        """最近のチャットメッセージ（CHAT タイプのみ）を取得する。

        Returns:
            {"speaker": str, "body": str} の dict リスト（古い順）。
        """
        raw = await self._get("/api/messages")
        if not isinstance(raw, list):
            return []

        result = []
        for msg in raw:
            # ロール・システムメッセージを除外
            if msg.get("type", 0) != _CHAT_TYPE_CHAT:
                continue
            speaker_obj = msg.get("speaker", {})
            speaker = (
                speaker_obj.get("alias")
                or speaker_obj.get("actor")
                or "unknown"
            )
            body = msg.get("content", "").strip()
            if body:
                result.append({"speaker": speaker, "body": body})

        return result

    async def take_screenshot(self) -> bytes | None:
        """シーンサムネイルを PNG バイト列として返す。

        Note:
            Foundry REST API はライブスクリーンショットをサポートしません。
            シーンのサムネイル画像（静的）を返します。
            リアルタイム画面制御が必要な場合は VisionVTTAdapter を使用してください。
        """
        if not self._active_scene_id:
            logger.info("take_screenshot: アクティブシーンなし")
            return None

        data = await self._get(f"/api/scenes/{self._active_scene_id}/thumbnail")
        if isinstance(data, bytes):
            return data

        logger.info(
            "take_screenshot: Foundry VTT はライブスクリーンショット非対応。"
            "VisionVTTAdapter を使用してください。"
        )
        return None

    def upload_asset(self, file_path: str, asset_type: str = "image") -> str | None:
        """ファイルを Foundry にアップロードし、アセット URL を返す。

        Note:
            同期的に呼び出し可能なラッパー（内部で asyncio.run を使用）。
        """
        import asyncio
        return asyncio.run(self._upload_asset_async(file_path, asset_type))

    async def _upload_asset_async(self, file_path: str, asset_type: str) -> str | None:
        """ファイルをマルチパートで Foundry にアップロードする。"""
        import aiofiles
        from pathlib import Path

        path = Path(file_path)
        if not path.exists():
            logger.warning("upload_asset: ファイルが存在しません: %s", file_path)
            return None

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                with open(path, "rb") as f:
                    resp = await client.post(
                        f"{self._base_url}/api/files/upload",
                        headers={k: v for k, v in self._headers.items() if k != "Content-Type"},
                        files={"file": (path.name, f, "application/octet-stream")},
                        data={"type": asset_type},
                    )
            if resp.status_code in (200, 201):
                data = resp.json()
                url = data.get("url") or data.get("path")
                logger.info("upload_asset: アップロード完了 → %s", url)
                return url
            logger.warning("upload_asset: HTTP %d", resp.status_code)
            return None
        except Exception as e:
            logger.error("upload_asset エラー: %s", e)
            return None
