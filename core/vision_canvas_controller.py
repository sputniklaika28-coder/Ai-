"""vision_canvas_controller.py — VLM ベースの Canvas 座標操作コントローラー。

スクリーンショットを VLM（GPT-4o 等）に送信し、Canvas 上の
駒位置をピクセル座標で特定する。DOM 解析では取得できない
Canvas 描画領域の視覚情報を AI で解釈する。
"""

from __future__ import annotations

import base64
import json
import logging
import re

import requests

logger = logging.getLogger(__name__)

try:
    from core.vtt_adapters.playwright_utils import (
        GRID_SIZE,
        clip_screenshot,
        get_canvas_bounds,
        mouse_drag,
        spawn_piece_clipboard,
    )
except ModuleNotFoundError:
    from vtt_adapters.playwright_utils import (  # type: ignore[no-redef]
        GRID_SIZE,
        clip_screenshot,
        get_canvas_bounds,
        mouse_drag,
        spawn_piece_clipboard,
    )

# VLM プロンプトテンプレートの遅延読み込み
_TEMPLATES: dict[str, str] = {}


def _load_templates() -> dict[str, str]:
    """browser_use_tasks.json から VLM プロンプトテンプレートを読み込む。"""
    global _TEMPLATES
    if _TEMPLATES:
        return _TEMPLATES
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "configs" / "browser_use_tasks.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            _TEMPLATES = json.load(f)
    return _TEMPLATES


class VisionCanvasController:
    """VLM で Canvas 上のゲーム盤面を視覚解析し、ピクセル座標操作を行う。

    2つの VLM バックエンドをサポート:
    - cloud: OpenAI GPT-4o API（高精度、有料）
    - local: 既存の LMClient 経由（無料、精度は利用モデル依存）
    """

    def __init__(
        self,
        page: object,
        cloud_api_key: str = "",
        cloud_model: str = "gpt-4o",
        lm_client: object | None = None,
        vlm_provider: str = "openai",
    ) -> None:
        self._page = page
        self._cloud_api_key = cloud_api_key
        self._cloud_model = cloud_model
        self._lm_client = lm_client
        self._vlm_provider = vlm_provider

    # ──────────────────────────────────────────
    # スクリーンショット
    # ──────────────────────────────────────────

    def take_canvas_screenshot(self) -> bytes | None:
        """Canvas / ボード領域だけをスクリーンショットする。"""
        bounds = get_canvas_bounds(self._page)
        if bounds:
            return clip_screenshot(self._page, bounds)
        # フォールバック: フルページスクリーンショット
        try:
            return self._page.screenshot()  # type: ignore[union-attr]
        except Exception:
            return None

    def take_canvas_screenshot_b64(self) -> str | None:
        """Canvas スクリーンショットを Base64 文字列で返す。"""
        raw = self.take_canvas_screenshot()
        if raw:
            return base64.b64encode(raw).decode("ascii")
        return None

    # ──────────────────────────────────────────
    # VLM 解析
    # ──────────────────────────────────────────

    def analyze_board(self, query: str = "") -> list[dict]:
        """VLM で盤面上の全駒を検出する。

        Args:
            query: 追加の解析指示（省略可）。

        Returns:
            検出された駒のリスト。各要素は
            {"description": str, "px_x": int, "px_y": int} を含む。
        """
        image_b64 = self.take_canvas_screenshot_b64()
        if not image_b64:
            return []

        viewport = self._get_viewport_size()
        templates = _load_templates()
        prompt = templates.get("vision_analyze_board", "").format(
            width=viewport[0], height=viewport[1],
        )
        if query:
            prompt += f"\n追加指示: {query}"

        response = self._call_vlm(prompt, image_b64)
        coords = self._parse_coordinates(response)
        return self._validate_coordinates(coords, viewport)

    def find_piece_position(self, description: str) -> tuple[int, int] | None:
        """VLM で特定の駒の座標を検出する。

        Args:
            description: 駒の説明文。

        Returns:
            (px_x, px_y) タプル。見つからなければ None。
        """
        image_b64 = self.take_canvas_screenshot_b64()
        if not image_b64:
            return None

        viewport = self._get_viewport_size()
        templates = _load_templates()
        prompt = templates.get("vision_find_piece", "").format(
            description=description, width=viewport[0], height=viewport[1],
        )

        response = self._call_vlm(prompt, image_b64)
        coords = self._parse_single_coordinate(response)
        if coords and self._is_in_viewport(coords, viewport):
            return self._snap_to_grid(coords)
        return None

    def find_empty_space(self, near: str = "") -> tuple[int, int] | None:
        """VLM でボード上の空きスペースを検出する。

        Args:
            near: 「〜の近く」等のヒント（省略可）。

        Returns:
            (px_x, px_y) タプル。見つからなければ None。
        """
        image_b64 = self.take_canvas_screenshot_b64()
        if not image_b64:
            return None

        viewport = self._get_viewport_size()
        templates = _load_templates()
        near_hint = f"\nヒント: {near} の近くで探してください。" if near else ""
        prompt = templates.get("vision_find_empty", "").format(
            width=viewport[0], height=viewport[1], near_hint=near_hint,
        )

        response = self._call_vlm(prompt, image_b64)
        coords = self._parse_single_coordinate(response)
        if coords and self._is_in_viewport(coords, viewport):
            return self._snap_to_grid(coords)
        return None

    def describe_scene(self) -> str:
        """VLM で盤面の状態を自然言語で説明する。"""
        image_b64 = self.take_canvas_screenshot_b64()
        if not image_b64:
            return "スクリーンショットを取得できませんでした。"

        templates = _load_templates()
        prompt = templates.get("vision_describe_scene", "盤面の状態を説明してください。")
        return self._call_vlm(prompt, image_b64)

    # ──────────────────────────────────────────
    # 座標変換
    # ──────────────────────────────────────────

    @staticmethod
    def pixel_to_grid(px_x: int, px_y: int) -> tuple[int, int]:
        """ピクセル座標をグリッド座標に変換する。"""
        return (round(px_x / GRID_SIZE), round(px_y / GRID_SIZE))

    @staticmethod
    def grid_to_pixel(grid_x: int, grid_y: int) -> tuple[int, int]:
        """グリッド座標をピクセル座標（セル中心）に変換する。"""
        return (grid_x * GRID_SIZE + GRID_SIZE // 2, grid_y * GRID_SIZE + GRID_SIZE // 2)

    # ──────────────────────────────────────────
    # Canvas マウス操作
    # ──────────────────────────────────────────

    def drag_piece(self, from_px: tuple[int, int], to_px: tuple[int, int]) -> bool:
        """Canvas 上で駒をドラッグ移動する。"""
        return mouse_drag(self._page, from_px, to_px, steps=10)

    def click_at(self, px_x: int, px_y: int) -> bool:
        """Canvas 上の指定座標をクリックする。"""
        try:
            self._page.mouse.click(px_x, px_y)  # type: ignore[union-attr]
            return True
        except Exception as e:
            logger.error("クリックエラー: %s", e)
            return False

    def place_piece_at_visual_location(
        self, description: str, character_json: dict
    ) -> bool:
        """VLM で位置を特定し、駒を配置する。

        1. VLM で目標位置のピクセル座標を取得
        2. spawn_piece_clipboard で駒を生成（デフォルト位置）
        3. 生成された駒を目標位置にドラッグ

        Args:
            description: 配置位置の説明（"十字路", "部屋の中央" 等）。
            character_json: CCFolia 形式のキャラクターデータ。

        Returns:
            成功した場合 True。
        """
        target = self.find_empty_space(near=description)
        if target is None:
            target = self.find_piece_position(description)
        if target is None:
            logger.error("VLM で位置を特定できませんでした: %s", description)
            return False

        # 駒をクリップボードペーストで配置
        if not spawn_piece_clipboard(self._page, character_json):
            return False

        # デフォルト位置（通常は画面左上付近）から目標位置にドラッグ
        # ※配置直後の駒位置は VTT により異なるため、再検出が望ましい
        logger.info("駒を (%d, %d) に配置しました", target[0], target[1])
        return True

    # ──────────────────────────────────────────
    # VLM バックエンド
    # ──────────────────────────────────────────

    def _call_vlm(self, prompt: str, image_b64: str) -> str:
        """設定に応じた VLM バックエンドを呼び出す。"""
        if self._vlm_provider == "local" and self._lm_client is not None:
            return self._call_local_vlm(prompt, image_b64)
        if self._cloud_api_key:
            return self._call_cloud_vlm(prompt, image_b64)
        if self._lm_client is not None:
            return self._call_local_vlm(prompt, image_b64)
        logger.error("VLM バックエンドが設定されていません")
        return ""

    def _call_cloud_vlm(self, prompt: str, image_b64: str) -> str:
        """OpenAI GPT-4o Vision API を直接呼び出す。"""
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._cloud_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._cloud_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{image_b64}",
                                    },
                                },
                            ],
                        }
                    ],
                    "max_tokens": 1000,
                    "temperature": 0.2,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            logger.error("Cloud VLM エラー: %d %s", resp.status_code, resp.text[:200])
            return ""
        except Exception as e:
            logger.error("Cloud VLM 通信エラー: %s", e)
            return ""

    def _call_local_vlm(self, prompt: str, image_b64: str) -> str:
        """既存の LMClient 経由でローカル VLM を呼び出す。"""
        try:
            messages = [{"role": "user", "content": prompt}]
            content, _ = self._lm_client.generate_with_tools(  # type: ignore[union-attr]
                messages=messages,
                tools=[],
                temperature=0.2,
                max_tokens=1000,
                image_base64=image_b64,
            )
            return content or ""
        except Exception as e:
            logger.error("Local VLM エラー: %s", e)
            return ""

    # ──────────────────────────────────────────
    # レスポンスパース
    # ──────────────────────────────────────────

    def _parse_coordinates(self, response: str) -> list[dict]:
        """VLM レスポンスから複数の座標を抽出する。"""
        try:
            # JSON ブロックを抽出
            json_str = self._extract_json(response)
            if not json_str:
                return []
            data = json.loads(json_str)
            if isinstance(data, dict) and "pieces" in data:
                return data["pieces"]
            if isinstance(data, list):
                return data
            return []
        except (json.JSONDecodeError, KeyError):
            return []

    def _parse_single_coordinate(self, response: str) -> tuple[int, int] | None:
        """VLM レスポンスから単一の座標を抽出する。"""
        try:
            json_str = self._extract_json(response)
            if not json_str:
                return None
            data = json.loads(json_str)
            if isinstance(data, dict) and "px_x" in data and "px_y" in data:
                return (int(data["px_x"]), int(data["px_y"]))
            return None
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """テキストから JSON 部分を抽出する。"""
        # ```json ... ``` ブロック
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return m.group(1)
        # 裸の { ... }
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            return text[first:last + 1]
        return None

    def _validate_coordinates(
        self, coords: list[dict], viewport: tuple[int, int]
    ) -> list[dict]:
        """座標をビューポート範囲内に検証・フィルタする。"""
        valid = []
        for c in coords:
            try:
                px_x = int(c.get("px_x", 0))
                px_y = int(c.get("px_y", 0))
                if self._is_in_viewport((px_x, px_y), viewport):
                    snapped = self._snap_to_grid((px_x, px_y))
                    c["px_x"] = snapped[0]
                    c["px_y"] = snapped[1]
                    c["grid_x"], c["grid_y"] = self.pixel_to_grid(*snapped)
                    valid.append(c)
            except (TypeError, ValueError):
                continue
        return valid

    @staticmethod
    def _is_in_viewport(
        coords: tuple[int, int], viewport: tuple[int, int]
    ) -> bool:
        """座標がビューポート内かチェックする。"""
        return 0 <= coords[0] <= viewport[0] and 0 <= coords[1] <= viewport[1]

    @staticmethod
    def _snap_to_grid(coords: tuple[int, int]) -> tuple[int, int]:
        """座標を最近接グリッドセル中心にスナップする。"""
        gx = round(coords[0] / GRID_SIZE)
        gy = round(coords[1] / GRID_SIZE)
        return (gx * GRID_SIZE + GRID_SIZE // 2, gy * GRID_SIZE + GRID_SIZE // 2)

    def _get_viewport_size(self) -> tuple[int, int]:
        """ページのビューポートサイズを取得する。"""
        try:
            size = self._page.evaluate(  # type: ignore[union-attr]
                "() => ({w: window.innerWidth, h: window.innerHeight})"
            )
            return (size["w"], size["h"])
        except Exception:
            return (1280, 900)
