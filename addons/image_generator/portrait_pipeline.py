"""portrait_pipeline.py — キャラクター立ち絵・トークン自動生成パイプライン。

Phase 2 実装: キャラクター設定テキスト（外見・職業・装備など）から
VTT で利用可能な背景透過のトークン画像や立ち絵を自動生成する。

パイプライン:
  1. CharacterConceptOutput.portrait_keywords → 英語プロンプト構築
  2. ComfyUI API → PNG 生成
  3. 背景除去（rembg 優先 / PIL フォールバック）
  4. 円形クロップ + ゴールドボーダー → トークン PNG
  5. outputs/ ディレクトリに保存

依存:
  pip install -e .[portrait]   → Pillow + rembg

Pillow のみ（rembg なし）でも動作するが、背景除去の精度が下がる。
"""

from __future__ import annotations

import io
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────
# スタイルプリセット
# ──────────────────────────────────────

PORTRAIT_STYLES: dict[str, dict[str, Any]] = {
    "anime_character": {
        "prefix": (
            "masterpiece, best quality, ultra detailed, "
            "anime style, 2d illustration, character portrait, "
            "upper body, centered, plain white background"
        ),
        "negative": (
            "low quality, blurry, deformed, extra limbs, "
            "realistic photo, 3d render, watermark, text, signature"
        ),
        "width": 768,
        "height": 1024,
    },
    "fantasy_portrait": {
        "prefix": (
            "masterpiece, best quality, digital painting, "
            "fantasy RPG character portrait, upper body, "
            "dramatic lighting, plain background"
        ),
        "negative": (
            "low quality, blurry, deformed, modern clothes, "
            "watermark, text, jpeg artifacts"
        ),
        "width": 768,
        "height": 1024,
    },
    "dark_gothic": {
        "prefix": (
            "masterpiece, best quality, dark fantasy, gothic style, "
            "character portrait, upper body, dark atmosphere, "
            "dramatic shadows, plain dark background"
        ),
        "negative": (
            "low quality, blurry, deformed, cute, bright colors, "
            "watermark, text"
        ),
        "width": 768,
        "height": 1024,
    },
    "token_simple": {
        "prefix": (
            "masterpiece, best quality, simple character icon, "
            "game token style, white background, front view, full body"
        ),
        "negative": (
            "low quality, blurry, deformed, complex background, "
            "watermark, text"
        ),
        "width": 512,
        "height": 512,
    },
}


def _style_dimensions(style: str) -> tuple[int, int]:
    """スタイル名から (width, height) を返す。env オーバーライド優先。

    COMFYUI_WIDTH / COMFYUI_HEIGHT が 0 より大きい場合に限り
    token_simple 以外のスタイルで上書きする（正方形のトークンは保護）。
    """
    preset = PORTRAIT_STYLES.get(style, PORTRAIT_STYLES["anime_character"])
    w = int(preset.get("width", 768))
    h = int(preset.get("height", 1024))
    if style == "token_simple":
        return w, h
    try:
        from core.config import get_comfyui_height, get_comfyui_width
        ow = get_comfyui_width()
        oh = get_comfyui_height()
        if ow > 0:
            w = ow
        if oh > 0:
            h = oh
    except ImportError:
        pass
    return w, h

_DEFAULT_NEGATIVE = (
    "low quality, blurry, deformed, extra limbs, "
    "watermark, text, signature, duplicate"
)


# ──────────────────────────────────────
# 結果データクラス
# ──────────────────────────────────────

@dataclass
class PortraitResult:
    """立ち絵・トークン生成の結果。"""

    success: bool
    portrait_path: str | None = None
    """背景透過済み立ち絵 PNG のパス。"""
    token_path: str | None = None
    """円形クロップ済みトークン PNG のパス。"""
    raw_path: str | None = None
    """背景除去前の元 PNG のパス（デバッグ用）。"""
    error: str | None = None
    elapsed_seconds: float = 0.0
    background_removed: bool = False
    """rembg/PIL で背景除去に成功したか。"""


# ──────────────────────────────────────
# 背景除去ユーティリティ
# ──────────────────────────────────────

def _try_rembg(png_bytes: bytes) -> bytes | None:
    """rembg を使って背景を除去する。失敗時は None を返す。"""
    try:
        from rembg import remove  # type: ignore[import-not-found]
        result = remove(png_bytes)
        logger.info("portrait_pipeline: rembg で背景除去成功")
        return result
    except ImportError:
        return None
    except Exception as e:
        logger.warning("rembg 背景除去失敗: %s", e)
        return None


def _pil_remove_background(png_bytes: bytes, threshold: int = 30) -> bytes | None:
    """PIL を使ってほぼ白/ほぼ黒の背景を除去する（単色背景向け簡易版）。

    Args:
        png_bytes: 入力 PNG バイト列。
        threshold: 「白に近い」と判断する輝度の閾値（0〜255）。

    Returns:
        背景透過済み PNG バイト列、または PIL 未インストール時 None。
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return None

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    data = img.getdata()

    new_data = []
    for r, g, b, a in data:
        # 白背景除去（RGB が全て threshold 以上）
        if r > 255 - threshold and g > 255 - threshold and b > 255 - threshold:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append((r, g, b, a))

    img.putdata(new_data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    logger.info("portrait_pipeline: PIL フォールバックで背景除去")
    return buf.getvalue()


def remove_background(png_bytes: bytes) -> tuple[bytes, bool]:
    """背景除去を試みる。rembg → PIL → 元データの優先順で処理する。

    Returns:
        (result_bytes, success): success は実際に背景除去できた場合 True。
    """
    # rembg 優先
    result = _try_rembg(png_bytes)
    if result is not None:
        return result, True

    # PIL フォールバック（白背景のみ有効）
    result = _pil_remove_background(png_bytes)
    if result is not None:
        return result, True

    # どちらも使えない場合は元データをそのまま返す
    logger.warning("portrait_pipeline: 背景除去スキップ（rembg/PIL 未インストール）")
    return png_bytes, False


# ──────────────────────────────────────
# トークン作成ユーティリティ
# ──────────────────────────────────────

def create_circular_token(
    png_bytes: bytes,
    size: int = 256,
    border_width: int = 6,
    border_color: tuple[int, int, int] = (200, 160, 60),
) -> bytes | None:
    """背景透過 PNG を円形にクロップし、ゴールドボーダーを付けたトークンを生成する。

    Args:
        png_bytes: 入力 PNG（背景透過推奨）。
        size: 出力トークンの一辺ピクセル数。
        border_width: ボーダーの幅（px）。
        border_color: ボーダーの RGB カラー。

    Returns:
        トークン PNG バイト列。PIL 未インストール時は None。
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("create_circular_token: Pillow 未インストール。スキップ。")
        return None

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")

    # 正方形にリサイズ（アスペクト比を維持してセンタークロップ）
    w, h = img.size
    min_side = min(w, h)
    left = (w - min_side) // 2
    top = (h - min_side) // 2
    img = img.crop((left, top, left + min_side, top + min_side))
    img = img.resize((size, size), Image.LANCZOS)

    # 円形マスクを作成
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)

    # マスク適用
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)

    # ボーダー描画
    if border_width > 0:
        border_draw = ImageDraw.Draw(result)
        half = border_width // 2
        border_draw.ellipse(
            (half, half, size - 1 - half, size - 1 - half),
            outline=(*border_color, 255),
            width=border_width,
        )

    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue()


# ──────────────────────────────────────
# PortraitPipeline 本体
# ──────────────────────────────────────

class PortraitPipeline:
    """キャラクター立ち絵・VTT トークンの自動生成パイプライン。

    ComfyUI → 背景除去 → 円形トークン の一連の処理を担当する。

    Args:
        comfyui_client: ComfyUIClient インスタンス。
        output_dir: 生成ファイルの保存先ディレクトリ。
        default_steps: デフォルトのサンプリングステップ数。
        default_cfg: デフォルト CFG スケール。
        token_size: デフォルトのトークンサイズ（px）。
        token_border_color: トークンのボーダー色 (R, G, B)。
    """

    def __init__(
        self,
        comfyui_client: Any,
        output_dir: str | Path = "generated_images",
        default_steps: int | None = None,
        default_cfg: float | None = None,
        token_size: int | None = None,
        token_border_color: tuple[int, int, int] | None = None,
    ) -> None:
        self._client = comfyui_client
        self._output_dir = Path(output_dir)

        # 未指定の場合は core/config の env 値を使用（未導入時はハードコード既定にフォールバック）
        try:
            from core.config import (
                get_comfyui_cfg,
                get_comfyui_steps,
                get_portrait_token_border,
                get_portrait_token_size,
            )
            env_steps = get_comfyui_steps()
            env_cfg = get_comfyui_cfg()
            env_token_size = get_portrait_token_size()
            env_token_border = get_portrait_token_border()
        except ImportError:
            env_steps, env_cfg, env_token_size, env_token_border = 20, 7.0, 256, (200, 160, 60)

        self._default_steps = default_steps if default_steps is not None else env_steps
        self._default_cfg = default_cfg if default_cfg is not None else env_cfg
        self._token_size = token_size if token_size is not None else env_token_size
        self._token_border_color = token_border_color if token_border_color is not None else env_token_border

        # サブディレクトリを作成
        (self._output_dir / "portraits").mkdir(parents=True, exist_ok=True)
        (self._output_dir / "tokens").mkdir(parents=True, exist_ok=True)
        (self._output_dir / "raw").mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────
    # プロンプト構築
    # ──────────────────────────────────────

    def build_portrait_prompt(
        self,
        character_name: str,
        portrait_keywords: list[str],
        style: str = "anime_character",
        extra_positive: str = "",
        extra_negative: str = "",
    ) -> tuple[str, str]:
        """キャラクター情報から ComfyUI 用プロンプトペアを構築する。

        Args:
            character_name: キャラクター名（ログ用）。
            portrait_keywords: 英語キーワードリスト（CharacterConceptOutput.portrait_keywords）。
            style: PORTRAIT_STYLES のキー。
            extra_positive: 追加の正プロンプト。
            extra_negative: 追加の負プロンプト。

        Returns:
            (positive_prompt, negative_prompt) のタプル。
        """
        preset = PORTRAIT_STYLES.get(style, PORTRAIT_STYLES["anime_character"])
        prefix = preset["prefix"]
        neg = preset["negative"]

        # キーワードを結合
        keyword_str = ", ".join(portrait_keywords) if portrait_keywords else "character"
        positive = f"{prefix}, {keyword_str}"
        if extra_positive:
            positive = f"{positive}, {extra_positive}"

        negative = neg
        if extra_negative:
            negative = f"{negative}, {extra_negative}"

        logger.debug(
            "build_portrait_prompt: [%s] positive='%s...'",
            character_name,
            positive[:60],
        )
        return positive, negative

    # ──────────────────────────────────────
    # メインパイプライン
    # ──────────────────────────────────────

    def generate_portrait(
        self,
        character_name: str,
        portrait_keywords: list[str],
        style: str = "anime_character",
        remove_bg: bool = True,
        create_token: bool = True,
        token_size: int | None = None,
        extra_positive: str = "",
        extra_negative: str = "",
    ) -> PortraitResult:
        """立ち絵を生成し、背景除去・トークン化を行う。

        同期 API（ComfyUIClient が同期実装のため）。

        Args:
            character_name: キャラクター名（ファイル名に使用）。
            portrait_keywords: 英語キーワードリスト。
            style: 生成スタイル（PORTRAIT_STYLES のキー）。
            remove_bg: 背景除去を行うか。
            create_token: 円形トークンを生成するか。
            token_size: トークンサイズ（None でデフォルト使用）。
            extra_positive / extra_negative: 追加プロンプト。

        Returns:
            PortraitResult。
        """
        t0 = time.time()
        safe_name = self._safe_filename(character_name)
        preset = PORTRAIT_STYLES.get(style, PORTRAIT_STYLES["anime_character"])

        # ComfyUI が利用可能か確認
        if not self._client.is_available():
            return PortraitResult(
                success=False,
                error="ComfyUI サーバーに接続できません。起動していることを確認してください。",
            )

        # プロンプト構築
        positive, negative = self.build_portrait_prompt(
            character_name,
            portrait_keywords,
            style=style,
            extra_positive=extra_positive,
            extra_negative=extra_negative,
        )

        # ComfyUI で生成（env 上書き考慮の寸法を使用）
        width, height = _style_dimensions(style)
        logger.info(
            "portrait_pipeline: '%s' の画像生成開始 (%dx%d)...",
            character_name, width, height,
        )
        gen_result = self._client.generate(
            prompt=positive,
            negative_prompt=negative,
            width=width,
            height=height,
            steps=self._default_steps,
            cfg=self._default_cfg,
        )

        if not gen_result.success or not gen_result.image_data:
            return PortraitResult(
                success=False,
                error=gen_result.error or "ComfyUI 画像生成失敗",
                elapsed_seconds=time.time() - t0,
            )

        raw_bytes = gen_result.image_data

        # 元画像を保存
        raw_path = self._output_dir / "raw" / f"{safe_name}_raw.png"
        raw_path.write_bytes(raw_bytes)

        # 背景除去
        bg_removed = False
        portrait_bytes = raw_bytes
        if remove_bg:
            portrait_bytes, bg_removed = remove_background(raw_bytes)

        # 立ち絵を保存
        portrait_path = self._output_dir / "portraits" / f"{safe_name}_portrait.png"
        portrait_path.write_bytes(portrait_bytes)
        logger.info("portrait_pipeline: 立ち絵保存 → %s", portrait_path)

        # トークン作成
        token_path_str: str | None = None
        if create_token:
            size = token_size or self._token_size
            token_bytes = create_circular_token(
                portrait_bytes,
                size=size,
                border_color=self._token_border_color,
            )
            if token_bytes is not None:
                token_path = self._output_dir / "tokens" / f"{safe_name}_token.png"
                token_path.write_bytes(token_bytes)
                token_path_str = str(token_path)
                logger.info("portrait_pipeline: トークン保存 → %s", token_path)
            else:
                logger.warning("portrait_pipeline: トークン生成スキップ（Pillow 未インストール）")

        elapsed = time.time() - t0
        logger.info(
            "portrait_pipeline: '%s' 完了 (%.1fs, bg_removed=%s)",
            character_name, elapsed, bg_removed,
        )

        return PortraitResult(
            success=True,
            portrait_path=str(portrait_path),
            token_path=token_path_str,
            raw_path=str(raw_path),
            elapsed_seconds=elapsed,
            background_removed=bg_removed,
        )

    def generate_from_character_json(
        self,
        character_json: dict,
        style: str = "anime_character",
        remove_bg: bool = True,
        create_token: bool = True,
    ) -> PortraitResult:
        """キャラクターシート dict から直接立ち絵を生成する。

        _persona.portrait_keywords が存在する場合はそれを使用する。
        なければ name + alias からキーワードを推定する。

        Args:
            character_json: PersonaBuilder.build_from_concept() が返した character_json。
        """
        name = character_json.get("name", "character")
        persona = character_json.get("_persona", {})
        keywords = persona.get("portrait_keywords", [])

        # キーワードがない場合は name / alias から生成
        if not keywords:
            alias = character_json.get("alias", "")
            keywords = [name, alias] if alias else [name]
            logger.info(
                "portrait_pipeline: portrait_keywords がないため name/alias を使用: %s",
                keywords,
            )

        return self.generate_portrait(
            character_name=name,
            portrait_keywords=keywords,
            style=style,
            remove_bg=remove_bg,
            create_token=create_token,
        )

    # ──────────────────────────────────────
    # ユーティリティ
    # ──────────────────────────────────────

    @staticmethod
    def _safe_filename(name: str, max_len: int = 40) -> str:
        """ファイル名として安全な文字列に変換する。"""
        safe = "".join(
            c if c.isalnum() or c in "-_." else "_"
            for c in name
        )
        return safe[:max_len].strip("_") or "character"
