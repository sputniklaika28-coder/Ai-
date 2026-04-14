"""gui_tab.py — 立ち絵・ペルソナ生成 GUI タブ。

customtkinter ベースの GUI タブ。
ランチャーのタブシステムから動的にロードされる。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


def build_tab(parent: Any, addon: Any) -> Any:
    """GUI タブを構築して返す。ランチャーから呼ばれる。

    Args:
        parent: 親ウィジェット（CTkFrame または tk.Frame）。
        addon: PortraitGeneratorAddon インスタンス。

    Returns:
        ウィジェット（parent に pack/grid 済み）。
    """
    try:
        import customtkinter as ctk  # type: ignore[import-not-found]
    except ImportError:
        import tkinter as tk

        frame = tk.Frame(parent, bg="#1e1e2e")
        label = tk.Label(
            frame,
            text="customtkinter が必要です。\npip install customtkinter",
            bg="#1e1e2e", fg="#cdd6f4",
        )
        label.pack(pady=20)
        frame.pack(fill="both", expand=True)
        return frame

    return _build_ctk_tab(parent, addon, ctk)


def _build_ctk_tab(parent: Any, addon: Any, ctk: Any) -> Any:
    frame = ctk.CTkFrame(parent)
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    # ── タイトル ──────────────────────────────
    title = ctk.CTkLabel(
        frame,
        text="キャラクター生成 / ペルソナ構築",
        font=ctk.CTkFont(size=16, weight="bold"),
    )
    title.pack(pady=(10, 5))

    # ── コンセプト入力 ────────────────────────
    concept_label = ctk.CTkLabel(frame, text="キャラクターコンセプト:")
    concept_label.pack(anchor="w", padx=15)

    concept_box = ctk.CTkTextbox(frame, height=80)
    concept_box.pack(fill="x", padx=15, pady=(0, 8))
    concept_box.insert("end", "例: 射撃が得意な無口な少女祓魔師")

    # ── プレイヤー名 ──────────────────────────
    name_frame = ctk.CTkFrame(frame, fg_color="transparent")
    name_frame.pack(fill="x", padx=15, pady=(0, 8))
    ctk.CTkLabel(name_frame, text="プレイヤー名（省略可）:").pack(side="left")
    player_name_var = ctk.StringVar()
    name_entry = ctk.CTkEntry(name_frame, textvariable=player_name_var, width=180)
    name_entry.pack(side="left", padx=(8, 0))

    # ── ステータス表示 ────────────────────────
    status_var = ctk.StringVar(value="待機中")
    status_label = ctk.CTkLabel(frame, textvariable=status_var, text_color="#a6e3a1")
    status_label.pack(pady=4)

    # ── 結果表示 ──────────────────────────────
    result_box = ctk.CTkTextbox(frame, height=200, state="disabled")
    result_box.pack(fill="both", expand=True, padx=15, pady=(0, 8))

    def _set_result(text: str) -> None:
        result_box.configure(state="normal")
        result_box.delete("1.0", "end")
        result_box.insert("end", text)
        result_box.configure(state="disabled")

    # ── ボタン群 ──────────────────────────────
    btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
    btn_frame.pack(fill="x", padx=15, pady=(0, 10))

    def _on_build_persona() -> None:
        concept = concept_box.get("1.0", "end").strip()
        if not concept:
            status_var.set("❌ コンセプトを入力してください")
            return
        status_var.set("⏳ ペルソナ生成中...")

        def run() -> None:
            # PersonaBuilder を直接使う（addon._persona_builder 経由）
            import asyncio
            pb = getattr(addon, "_persona_builder", None)
            if pb is None:
                frame.after(0, lambda: status_var.set("❌ PersonaBuilder が未初期化"))
                return
            try:
                result = asyncio.run(
                    pb.build_from_concept(
                        concept_text=concept,
                        player_name=player_name_var.get(),
                    )
                )
            except Exception as e:
                frame.after(0, lambda: status_var.set(f"❌ エラー: {e}"))
                return

            if result is None:
                frame.after(0, lambda: status_var.set("❌ 生成失敗"))
                return

            summary = (
                f"【{result.character_name}】{result.persona_summary}\n\n"
                f"--- システムプロンプト ---\n{result.system_prompt}\n\n"
                f"--- 話し方サンプル ---\n"
                + "\n".join(f"・{s}" for s in result.speech_style_examples)
            )
            frame.after(0, lambda: (
                status_var.set(f"✓ 生成完了: {result.character_name}"),
                _set_result(summary),
            ))

        threading.Thread(target=run, daemon=True).start()

    ctk.CTkButton(
        btn_frame,
        text="ペルソナ生成",
        command=_on_build_persona,
        fg_color="#89b4fa",
        text_color="#1e1e2e",
        width=140,
    ).pack(side="left", padx=(0, 8))

    def _on_generate_portrait() -> None:
        # コンセプトからキャラクター名を推定（概念的な実装）
        status_var.set("⏳ 立ち絵生成中（ComfyUI が必要）...")

        def run() -> None:
            pipeline = getattr(addon, "_pipeline", None)
            if pipeline is None:
                frame.after(0, lambda: status_var.set("❌ ComfyUI が起動していません"))
                return
            concept = concept_box.get("1.0", "end").strip()
            # コンセプトを英語キーワードとして直接使う簡易版
            result = pipeline.generate_portrait(
                character_name="portrait",
                portrait_keywords=[concept[:100]],
                style="anime_character",
            )
            if result.success:
                frame.after(0, lambda: (
                    status_var.set("✓ 立ち絵生成完了"),
                    _set_result(
                        f"立ち絵: {result.portrait_path}\n"
                        f"トークン: {result.token_path}\n"
                        f"時間: {result.elapsed_seconds:.1f}s\n"
                        f"背景除去: {'成功' if result.background_removed else '未実施'}"
                    ),
                ))
            else:
                frame.after(0, lambda: status_var.set(f"❌ {result.error}"))

        threading.Thread(target=run, daemon=True).start()

    ctk.CTkButton(
        btn_frame,
        text="立ち絵生成",
        command=_on_generate_portrait,
        fg_color="#a6e3a1",
        text_color="#1e1e2e",
        width=140,
    ).pack(side="left")

    frame.pack(fill="both", expand=True)
    return frame
