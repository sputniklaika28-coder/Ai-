"""comfyui_client.py — ComfyUI API クライアント。

ComfyUI の REST API と WebSocket を利用して画像生成を行う軽量クライアント。
ComfyUI がローカルで起動していることを前提とする (デフォルト: http://127.0.0.1:8188)。

主な機能:
  - ワークフロー (prompt) のキューイング
  - 生成ステータスのポーリング
  - 生成画像のダウンロード
  - ヘルスチェック
"""

from __future__ import annotations

import io
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# デフォルトのテキスト→画像ワークフロー (SD1.5 / SDXL 汎用)
DEFAULT_WORKFLOW: dict[str, Any] = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "low quality, blurry, deformed, watermark, text",
            "clip": ["4", 1],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "trpg_gen", "images": ["8", 0]},
    },
}


@dataclass
class GenerationResult:
    """画像生成の結果。"""

    success: bool
    image_path: str | None = None
    image_data: bytes | None = None
    prompt_id: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0


@dataclass
class ComfyUIConfig:
    """ComfyUI 接続設定。"""

    host: str = "127.0.0.1"
    port: int = 8188
    checkpoint: str = "sd_xl_base_1.0.safetensors"
    default_width: int = 1024
    default_height: int = 1024
    default_steps: int = 20
    default_cfg: float = 7.0
    timeout: int = 120
    poll_interval: float = 1.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class ComfyUIClient:
    """ComfyUI REST API クライアント。"""

    def __init__(self, config: ComfyUIConfig | None = None):
        self.config = config or ComfyUIConfig()
        self._client_id = str(uuid.uuid4())

    # ──────────────────────────────────────
    # ヘルスチェック
    # ──────────────────────────────────────

    def is_available(self) -> bool:
        """ComfyUI サーバーが起動しているか確認。"""
        try:
            r = requests.get(
                f"{self.config.base_url}/system_stats",
                timeout=3,
            )
            return r.status_code == 200
        except (requests.ConnectionError, requests.Timeout):
            return False

    def get_system_stats(self) -> dict:
        """サーバーシステム統計を取得。"""
        try:
            r = requests.get(
                f"{self.config.base_url}/system_stats",
                timeout=5,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("ComfyUI system_stats 取得失敗: %s", e)
            return {}

    def get_checkpoints(self) -> list[str]:
        """利用可能なチェックポイント一覧を取得。"""
        try:
            r = requests.get(
                f"{self.config.base_url}/object_info/CheckpointLoaderSimple",
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            return (
                data.get("CheckpointLoaderSimple", {})
                .get("input", {})
                .get("required", {})
                .get("ckpt_name", [[]])[0]
            )
        except Exception as e:
            logger.warning("チェックポイント一覧の取得失敗: %s", e)
            return []

    # ──────────────────────────────────────
    # ワークフロー構築
    # ──────────────────────────────────────

    def build_workflow(
        self,
        prompt: str,
        negative_prompt: str = "low quality, blurry, deformed, watermark, text",
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        cfg: float | None = None,
        seed: int | None = None,
        checkpoint: str | None = None,
    ) -> dict:
        """デフォルトワークフローをパラメータで上書きして返す。"""
        import copy
        import random

        workflow = copy.deepcopy(DEFAULT_WORKFLOW)

        # チェックポイント
        workflow["4"]["inputs"]["ckpt_name"] = checkpoint or self.config.checkpoint

        # 画像サイズ
        workflow["5"]["inputs"]["width"] = width or self.config.default_width
        workflow["5"]["inputs"]["height"] = height or self.config.default_height

        # プロンプト
        workflow["6"]["inputs"]["text"] = prompt
        workflow["7"]["inputs"]["text"] = negative_prompt

        # サンプラー設定
        workflow["3"]["inputs"]["steps"] = steps or self.config.default_steps
        workflow["3"]["inputs"]["cfg"] = cfg or self.config.default_cfg
        workflow["3"]["inputs"]["seed"] = seed if seed is not None else random.randint(0, 2**32 - 1)

        return workflow

    # ──────────────────────────────────────
    # キューイング & ポーリング
    # ──────────────────────────────────────

    def queue_prompt(self, workflow: dict) -> str:
        """ワークフローをキューに投入し、prompt_id を返す。"""
        payload = {
            "prompt": workflow,
            "client_id": self._client_id,
        }
        r = requests.post(
            f"{self.config.base_url}/prompt",
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        prompt_id = data.get("prompt_id", "")
        if not prompt_id:
            raise RuntimeError(f"prompt_id が返されませんでした: {data}")
        logger.info("キュー投入: prompt_id=%s", prompt_id)
        return prompt_id

    def poll_until_done(self, prompt_id: str) -> dict:
        """生成完了までポーリングし、history エントリを返す。"""
        deadline = time.time() + self.config.timeout
        while time.time() < deadline:
            try:
                r = requests.get(
                    f"{self.config.base_url}/history/{prompt_id}",
                    timeout=5,
                )
                r.raise_for_status()
                history = r.json()
                if prompt_id in history:
                    entry = history[prompt_id]
                    status = entry.get("status", {})
                    if status.get("completed", False) or status.get("status_str") == "success":
                        return entry
                    if status.get("status_str") == "error":
                        raise RuntimeError(
                            f"生成エラー: {status.get('messages', [])}"
                        )
            except requests.ConnectionError:
                logger.warning("ポーリング中に接続エラー、リトライ...")
            time.sleep(self.config.poll_interval)

        raise TimeoutError(
            f"画像生成がタイムアウトしました ({self.config.timeout}秒)"
        )

    # ──────────────────────────────────────
    # 画像取得
    # ──────────────────────────────────────

    def get_images_from_history(self, history_entry: dict) -> list[dict]:
        """history エントリから画像ファイル情報を抽出。"""
        images = []
        outputs = history_entry.get("outputs", {})
        for node_id, node_output in outputs.items():
            for img in node_output.get("images", []):
                images.append({
                    "filename": img.get("filename", ""),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                })
        return images

    def download_image(self, filename: str, subfolder: str = "", img_type: str = "output") -> bytes:
        """生成画像をバイト列としてダウンロード。"""
        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": img_type,
        }
        r = requests.get(
            f"{self.config.base_url}/view",
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        return r.content

    def save_image(self, image_data: bytes, output_path: str | Path) -> Path:
        """画像をローカルに保存。"""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_data)
        logger.info("画像保存: %s (%d bytes)", path, len(image_data))
        return path

    # ──────────────────────────────────────
    # 統合 API: テキスト→画像
    # ──────────────────────────────────────

    def generate(
        self,
        prompt: str,
        negative_prompt: str = "low quality, blurry, deformed, watermark, text",
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        cfg: float | None = None,
        seed: int | None = None,
        checkpoint: str | None = None,
        output_dir: str | Path | None = None,
    ) -> GenerationResult:
        """テキストプロンプトから画像を生成する統合メソッド。"""
        start_time = time.time()

        if not self.is_available():
            return GenerationResult(
                success=False,
                error="ComfyUI サーバーに接続できません。起動しているか確認してください。",
            )

        try:
            workflow = self.build_workflow(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                seed=seed,
                checkpoint=checkpoint,
            )

            prompt_id = self.queue_prompt(workflow)
            history_entry = self.poll_until_done(prompt_id)
            images = self.get_images_from_history(history_entry)

            if not images:
                return GenerationResult(
                    success=False,
                    prompt_id=prompt_id,
                    error="画像が生成されませんでした",
                    elapsed_seconds=time.time() - start_time,
                )

            # 最初の画像を取得
            img_info = images[0]
            image_data = self.download_image(
                filename=img_info["filename"],
                subfolder=img_info["subfolder"],
                img_type=img_info["type"],
            )

            image_path = None
            if output_dir:
                out = Path(output_dir) / img_info["filename"]
                self.save_image(image_data, out)
                image_path = str(out)

            return GenerationResult(
                success=True,
                image_path=image_path,
                image_data=image_data,
                prompt_id=prompt_id,
                elapsed_seconds=time.time() - start_time,
            )

        except TimeoutError as e:
            return GenerationResult(
                success=False, error=str(e), elapsed_seconds=time.time() - start_time
            )
        except Exception as e:
            logger.exception("画像生成中にエラー")
            return GenerationResult(
                success=False, error=str(e), elapsed_seconds=time.time() - start_time
            )
