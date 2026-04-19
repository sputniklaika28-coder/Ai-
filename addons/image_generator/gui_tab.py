"""画像生成 GUI タブ。

tkinter ベースの GUI タブで、手動プロンプト入力・スタイル選択・
生成結果プレビュー機能を提供する。
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

logger = logging.getLogger(__name__)


class ImageGeneratorTab(ttk.Frame):
    """画像生成タブ。"""

    def __init__(self, parent: tk.Widget, addon: Any = None, **kwargs):
        super().__init__(parent, **kwargs)
        self._addon = addon
        self._generating = False
        self._build_ui()

    def _build_ui(self) -> None:
        # ── プロンプト入力 ──
        prompt_frame = ttk.LabelFrame(self, text="プロンプト", padding=5)
        prompt_frame.pack(fill="x", padx=5, pady=5)

        self._prompt_text = tk.Text(prompt_frame, height=3, wrap="word")
        self._prompt_text.pack(fill="x", padx=2, pady=2)
        self._prompt_text.insert("1.0", "a fantasy dungeon entrance, dramatic lighting")

        # ── ネガティブプロンプト ──
        neg_frame = ttk.LabelFrame(self, text="ネガティブプロンプト", padding=5)
        neg_frame.pack(fill="x", padx=5, pady=2)

        self._negative_text = tk.Text(neg_frame, height=2, wrap="word")
        self._negative_text.pack(fill="x", padx=2, pady=2)
        self._negative_text.insert("1.0", "low quality, blurry, deformed, watermark, text")

        # ── スタイル選択 ──
        style_frame = ttk.Frame(self)
        style_frame.pack(fill="x", padx=5, pady=2)

        ttk.Label(style_frame, text="スタイル:").pack(side="left", padx=(0, 5))
        self._style_var = tk.StringVar(value="(なし)")
        style_names = ["(なし)", "ファンタジー風景", "ダークゴシック",
                       "アニメキャラクター", "戦術マップ", "水彩画風"]
        self._style_combo = ttk.Combobox(
            style_frame, textvariable=self._style_var,
            values=style_names, state="readonly", width=20,
        )
        self._style_combo.pack(side="left")

        self._style_key_map = {
            "(なし)": "",
            "ファンタジー風景": "fantasy_landscape",
            "ダークゴシック": "dark_gothic",
            "アニメキャラクター": "anime_character",
            "戦術マップ": "tactical_map",
            "水彩画風": "watercolor",
        }

        # ── パラメータ ──
        param_frame = ttk.Frame(self)
        param_frame.pack(fill="x", padx=5, pady=2)

        ttk.Label(param_frame, text="幅:").pack(side="left")
        self._width_var = tk.StringVar(value="1024")
        ttk.Entry(param_frame, textvariable=self._width_var, width=6).pack(side="left", padx=(0, 10))

        ttk.Label(param_frame, text="高さ:").pack(side="left")
        self._height_var = tk.StringVar(value="1024")
        ttk.Entry(param_frame, textvariable=self._height_var, width=6).pack(side="left", padx=(0, 10))

        ttk.Label(param_frame, text="ステップ:").pack(side="left")
        self._steps_var = tk.StringVar(value="20")
        ttk.Entry(param_frame, textvariable=self._steps_var, width=5).pack(side="left")

        # ── 生成ボタン ──
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=5, pady=5)

        self._generate_btn = ttk.Button(
            btn_frame, text="🎨 生成", command=self._on_generate
        )
        self._generate_btn.pack(side="left", padx=5)

        self._status_label = ttk.Label(btn_frame, text="待機中")
        self._status_label.pack(side="left", padx=10)

        # ── 結果表示 ──
        result_frame = ttk.LabelFrame(self, text="結果", padding=5)
        result_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self._result_text = tk.Text(result_frame, height=5, wrap="word", state="disabled")
        self._result_text.pack(fill="both", expand=True, padx=2, pady=2)

    def _on_generate(self) -> None:
        """生成ボタン押下。"""
        if self._generating:
            return
        if not self._addon:
            self._set_result("エラー: アドオンが初期化されていません")
            return

        self._generating = True
        self._generate_btn.config(state="disabled")
        self._status_label.config(text="生成中...")

        prompt = self._prompt_text.get("1.0", "end-1c").strip()
        negative = self._negative_text.get("1.0", "end-1c").strip()
        style_key = self._style_key_map.get(self._style_var.get(), "")

        try:
            width = int(self._width_var.get())
            height = int(self._height_var.get())
            steps = int(self._steps_var.get())
        except ValueError:
            self._set_result("エラー: パラメータは整数で入力してください")
            self._generating = False
            self._generate_btn.config(state="normal")
            self._status_label.config(text="待機中")
            return

        def _run():
            try:
                from .addon import IMAGE_STYLES
                from .comfyui_client import ComfyUIClient, ComfyUIConfig

                client = ComfyUIClient(ComfyUIConfig())

                full_prompt = prompt
                full_negative = negative
                if style_key and style_key in IMAGE_STYLES:
                    style = IMAGE_STYLES[style_key]
                    full_prompt += style["suffix"]
                    if not full_negative:
                        full_negative = style["negative"]

                result = client.generate(
                    prompt=full_prompt,
                    negative_prompt=full_negative,
                    width=width,
                    height=height,
                    steps=steps,
                    output_dir=self._addon._output_dir if self._addon else None,
                )

                if result.success:
                    msg = (
                        f"✓ 生成完了 ({result.elapsed_seconds:.1f}秒)\n"
                        f"ファイル: {result.image_path}\n"
                        f"Prompt ID: {result.prompt_id}"
                    )
                else:
                    msg = f"✗ 生成失敗: {result.error}"

                self.after(0, lambda: self._set_result(msg))
            except Exception as e:
                self.after(0, lambda: self._set_result(f"✗ エラー: {e}"))
            finally:
                self.after(0, self._generation_done)

        threading.Thread(target=_run, daemon=True).start()

    def _generation_done(self) -> None:
        self._generating = False
        self._generate_btn.config(state="normal")
        self._status_label.config(text="待機中")

    def _set_result(self, text: str) -> None:
        self._result_text.config(state="normal")
        self._result_text.delete("1.0", "end")
        self._result_text.insert("1.0", text)
        self._result_text.config(state="disabled")
