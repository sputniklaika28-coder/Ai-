"""
ccfolia_map_controller.py — リファクタ版
pyautogui / DPI計算を完全排除。
駒操作はVTTアダプターに委譲するための薄いインターフェース層として維持。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.vtt_adapters.base_adapter import BaseVTTAdapter

logger = logging.getLogger(__name__)
GRID_SIZE = 96


class CCFoliaMapController:
    """マップ操作のツール定義とディスパッチを提供するコントローラー。

    実際のブラウザ操作は VTTAdapter に委譲される。
    レガシー互換のため get_board_state / move_piece 等の
    シグネチャを維持するが、内部では adapter を呼び出す。
    """

    def __init__(self, adapter: BaseVTTAdapter | None = None):
        self.adapter = adapter

    @staticmethod
    def parse_xy(style: str | None) -> tuple[int, int]:
        """CSS transform translate(Xpx, Ypx) からピクセル座標を抽出する。"""
        m = re.search(r"translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)", style or "")
        return (int(float(m.group(1))), int(float(m.group(2)))) if m else (0, 0)

    @staticmethod
    def extract_hash(url: str | None) -> str:
        """CCFolia画像URLから8文字ハッシュを抽出する。"""
        m = re.search(r"/(?:shared|files)/([a-f0-9]+)", url or "")
        return m.group(1)[:8] if m else ""

    # ──────────────────────────────────────────
    # 公開API（アダプターに委譲）
    # ──────────────────────────────────────────

    def get_board_state(self) -> list[dict]:
        """全駒の位置情報を取得する。"""
        if self.adapter is None:
            logger.warning("アダプター未設定: get_board_state")
            return []
        return self.adapter.get_board_state()

    def move_piece(self, img_hash: str, grid_x: int, grid_y: int) -> bool:
        """img_hashで駒を特定してグリッド座標に移動する。"""
        if self.adapter is None:
            logger.warning("アダプター未設定: move_piece")
            return False
        return self.adapter.move_piece(img_hash, grid_x, grid_y)

    def move_piece_by_current_pos(
        self, cur_grid_x: int, cur_grid_y: int, dst_grid_x: int, dst_grid_y: int
    ) -> bool:
        """現在グリッド座標で駒を特定して移動する。"""
        if self.adapter is None:
            logger.warning("アダプター未設定: move_piece_by_current_pos")
            return False
        # アダプターの move_piece は piece_id ベースだが、
        # 座標指定の場合はボード状態から駒を検索して移動する
        state = self.adapter.get_board_state()
        targets = [p for p in state if p["grid_x"] == cur_grid_x and p["grid_y"] == cur_grid_y]
        if not targets:
            logger.warning("座標(%d,%d)に駒なし", cur_grid_x, cur_grid_y)
            return False
        target = targets[0]
        return self.adapter.move_piece(target["img_hash"], dst_grid_x, dst_grid_y)

    def pan_map(self, direction: str, grid_amount: int = 1) -> bool:
        """マップをスクロールする。アダプター経由でキー操作を送信。"""
        if self.adapter is None:
            logger.warning("アダプター未設定: pan_map")
            return False
        # pan_map はアダプターの低レベル操作として実装
        # アダプターが対応していない場合は False を返す
        if hasattr(self.adapter, "pan_map"):
            return self.adapter.pan_map(direction, grid_amount)
        logger.warning("アダプターが pan_map に対応していません")
        return False

    def spawn_piece(self, character_json: dict) -> bool:
        """キャラクターJSONをCCFoliaに配置する。"""
        if self.adapter is None:
            logger.warning("アダプター未設定: spawn_piece")
            return False
        return self.adapter.spawn_piece(character_json)


# ── エージェント用ツール定義 ────────────────────────────────

MAP_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_board_state",
            "description": "全駒の位置情報を取得",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_piece",
            "description": "img_hashで駒を特定してグリッド座標に移動",
            "parameters": {
                "type": "object",
                "properties": {
                    "img_hash": {"type": "string"},
                    "grid_x": {"type": "integer"},
                    "grid_y": {"type": "integer"},
                },
                "required": ["img_hash", "grid_x", "grid_y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_piece_by_current_pos",
            "description": "現在グリッド座標で駒を特定して移動",
            "parameters": {
                "type": "object",
                "properties": {
                    "cur_grid_x": {"type": "integer"},
                    "cur_grid_y": {"type": "integer"},
                    "dst_grid_x": {"type": "integer"},
                    "dst_grid_y": {"type": "integer"},
                },
                "required": ["cur_grid_x", "cur_grid_y", "dst_grid_x", "dst_grid_y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pan_map",
            "description": "マップをスクロール",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "grid_amount": {"type": "integer"},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_piece",
            "description": "キャラクターJSONをCCFoliaに配置する",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_json": {
                        "type": "object",
                        "description": "CCFolia形式のキャラクターデータ",
                    },
                },
                "required": ["character_json"],
            },
        },
    },
]


def execute_map_tool(ctrl: CCFoliaMapController, tool_name: str, args: dict) -> object:
    """ツール名に基づいてマップ操作を実行するディスパッチャー。"""
    if tool_name == "get_board_state":
        return ctrl.get_board_state()
    if tool_name == "move_piece":
        return ctrl.move_piece(**args)
    if tool_name == "move_piece_by_current_pos":
        return ctrl.move_piece_by_current_pos(**args)
    if tool_name == "pan_map":
        return ctrl.pan_map(**args)
    if tool_name == "spawn_piece":
        return ctrl.spawn_piece(**args)
    return {"error": f"未知のツール: {tool_name}"}
