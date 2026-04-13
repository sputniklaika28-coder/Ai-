"""VLM OS Agent GUI タブ。

- 対象ウィンドウドロップダウン（手動更新）
- 直近スクショのプレビュー（SoM トグル）
- タスク入力欄 + Start / Stop
- 座標キャッシュ一覧テーブル（行毎 invalidate）
- キルスイッチ LED
"""

from __future__ import annotations

import io
import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any

logger = logging.getLogger(__name__)


class VlmOsAgentTab(ttk.Frame):
    """VLM OS Agent の操作 GUI。"""

    def __init__(self, parent: tk.Widget, addon: Any = None, **kwargs):
        super().__init__(parent, **kwargs)
        self._addon = addon
        self._running = False
        self._preview_photo: Any = None  # GC 防止用参照
        self._build_ui()
        self._schedule_led_poll()

    # ──────────────────────────────────────
    # UI 構築
    # ──────────────────────────────────────

    def _build_ui(self) -> None:
        # ── 上部: ウィンドウ選択＋LED ──
        top = ttk.Frame(self)
        top.pack(fill="x", padx=5, pady=5)

        ttk.Label(top, text="対象ウィンドウ:").pack(side="left", padx=(0, 5))
        self._window_var = tk.StringVar(value="")
        self._window_combo = ttk.Combobox(
            top, textvariable=self._window_var, values=[], width=40,
        )
        self._window_combo.pack(side="left", padx=(0, 5))

        ttk.Button(top, text="更新", width=6, command=self._refresh_windows).pack(
            side="left", padx=(0, 5),
        )
        ttk.Button(top, text="スクショ", width=8, command=self._on_screenshot).pack(
            side="left", padx=(0, 10),
        )

        # キルスイッチ LED
        ttk.Label(top, text="KillSwitch:").pack(side="left", padx=(10, 3))
        self._led_canvas = tk.Canvas(top, width=16, height=16, highlightthickness=0)
        self._led_canvas.pack(side="left")
        self._led_oval = self._led_canvas.create_oval(
            2, 2, 14, 14, fill="#2ecc71", outline="#000000",
        )
        self._led_status_var = tk.StringVar(value="待機")
        ttk.Label(top, textvariable=self._led_status_var).pack(side="left", padx=5)

        # ── タスク実行欄 ──
        task_frame = ttk.LabelFrame(self, text="タスク実行", padding=5)
        task_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(task_frame, text="ゴール:").pack(anchor="w")
        self._goal_text = tk.Text(task_frame, height=3, wrap="word")
        self._goal_text.pack(fill="x", padx=2, pady=2)
        self._goal_text.insert("1.0", "チャットで『こんにちは』と送信")

        task_opt = ttk.Frame(task_frame)
        task_opt.pack(fill="x", pady=2)
        ttk.Label(task_opt, text="最大ステップ:").pack(side="left")
        self._max_steps_var = tk.StringVar(value="20")
        ttk.Entry(task_opt, textvariable=self._max_steps_var, width=5).pack(
            side="left", padx=(2, 10),
        )
        self._use_cache_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            task_opt, text="キャッシュを使う", variable=self._use_cache_var,
        ).pack(side="left")
        self._som_preview_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            task_opt, text="SoM プレビュー", variable=self._som_preview_var,
        ).pack(side="left", padx=(10, 0))

        task_btn = ttk.Frame(task_frame)
        task_btn.pack(fill="x", pady=2)
        self._start_btn = ttk.Button(
            task_btn, text="▶ Start", command=self._on_start,
        )
        self._start_btn.pack(side="left", padx=2)
        self._stop_btn = ttk.Button(
            task_btn, text="■ Stop (ESC)", command=self._on_stop, state="disabled",
        )
        self._stop_btn.pack(side="left", padx=2)
        self._status_var = tk.StringVar(value="待機中")
        ttk.Label(task_btn, textvariable=self._status_var).pack(side="left", padx=10)

        # ── 下段（左: プレビュー / 右: キャッシュ） ──
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=5, pady=5)

        # プレビュー
        preview_frame = ttk.LabelFrame(body, text="直近スクショ", padding=5)
        preview_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self._preview_label = ttk.Label(preview_frame, text="(スクショ未取得)")
        self._preview_label.pack(fill="both", expand=True)
        self._preview_info = tk.StringVar(value="")
        ttk.Label(preview_frame, textvariable=self._preview_info).pack(anchor="w")

        # キャッシュ
        cache_frame = ttk.LabelFrame(body, text="座標キャッシュ", padding=5)
        cache_frame.pack(side="left", fill="both", expand=True)

        cache_btns = ttk.Frame(cache_frame)
        cache_btns.pack(fill="x", pady=2)
        ttk.Button(cache_btns, text="再読込", width=8, command=self._refresh_cache).pack(
            side="left", padx=2,
        )
        ttk.Button(
            cache_btns, text="選択行を破棄", command=self._invalidate_selected,
        ).pack(side="left", padx=2)
        ttk.Button(cache_btns, text="全削除", command=self._clear_cache).pack(
            side="left", padx=2,
        )

        cols = ("desc", "window", "px", "hits", "age")
        self._cache_tree = ttk.Treeview(
            cache_frame, columns=cols, show="headings", height=12,
        )
        self._cache_tree.heading("desc", text="description")
        self._cache_tree.heading("window", text="window")
        self._cache_tree.heading("px", text="(x,y)")
        self._cache_tree.heading("hits", text="hits")
        self._cache_tree.heading("age", text="age(s)")
        self._cache_tree.column("desc", width=160, stretch=True)
        self._cache_tree.column("window", width=100, stretch=True)
        self._cache_tree.column("px", width=80, anchor="center")
        self._cache_tree.column("hits", width=50, anchor="center")
        self._cache_tree.column("age", width=60, anchor="center")
        self._cache_tree.pack(fill="both", expand=True, padx=2, pady=2)

        # ── 結果表示 ──
        log_frame = ttk.LabelFrame(self, text="結果 / ログ", padding=5)
        log_frame.pack(fill="x", padx=5, pady=5)
        self._log_text = tk.Text(log_frame, height=6, wrap="word", state="disabled")
        self._log_text.pack(fill="x", padx=2, pady=2)

        # 初期化
        self._refresh_windows()
        self._refresh_cache()

    # ──────────────────────────────────────
    # ハンドラ
    # ──────────────────────────────────────

    def _refresh_windows(self) -> None:
        try:
            from .window_focus import list_window_titles
            titles = list_window_titles()
        except Exception as e:
            logger.debug("ウィンドウ列挙失敗: %s", e)
            titles = []
        self._window_combo.configure(values=titles)
        if not self._window_var.get() and titles:
            # .env 既定値があれば優先
            default = ""
            try:
                if self._addon is not None and self._addon._settings is not None:
                    default = self._addon._settings.target_window or ""
            except AttributeError:
                pass
            self._window_var.set(default)

    def _on_screenshot(self) -> None:
        runtime = self._get_runtime()
        if runtime is None:
            self._log("エラー: AgentRuntime が利用できません（extras 未導入の可能性）")
            return
        title = self._window_var.get().strip() or None

        def _run() -> None:
            try:
                # プレビュー用に SoM トグルを反映
                prev_som = runtime._settings.som_enabled
                if self._som_preview_var.get():
                    runtime._settings.som_enabled = True  # noqa: SLF001
                try:
                    frame = runtime.screenshot(window_title=title)
                finally:
                    runtime._settings.som_enabled = prev_som  # noqa: SLF001
                self.after(0, lambda f=frame: self._update_preview(f))
            except Exception as exc:
                msg = f"スクショ失敗: {exc}"
                self.after(0, lambda m=msg: self._log(m))

        threading.Thread(target=_run, daemon=True).start()

    def _update_preview(self, frame: Any) -> None:
        try:
            from PIL import Image, ImageTk  # type: ignore[import-not-found]
        except ImportError:
            self._log("Pillow が未導入のためプレビュー表示不可")
            return
        png_bytes = frame.png_bytes
        if self._som_preview_var.get() and frame.perceive is not None:
            png_bytes = frame.perceive.annotated_png
        try:
            img = Image.open(io.BytesIO(png_bytes))
            w, h = img.size
            # プレビューは最大 500x400 にフィット
            max_w, max_h = 500, 400
            scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)))
            self._preview_photo = ImageTk.PhotoImage(img)
            self._preview_label.configure(image=self._preview_photo, text="")
        except Exception as e:
            self._log(f"プレビュー生成失敗: {e}")
            return
        self._preview_info.set(
            f"{frame.viewport[0]}x{frame.viewport[1]}  phash={frame.phash[:16]}…  "
            f"window='{frame.window_title}'",
        )

    def _on_start(self) -> None:
        if self._running:
            return
        runtime = self._get_runtime()
        if runtime is None:
            self._log("エラー: AgentRuntime が利用できません")
            return
        goal = self._goal_text.get("1.0", "end-1c").strip()
        if not goal:
            self._log("ゴールを入力してください")
            return
        try:
            max_steps = int(self._max_steps_var.get())
        except ValueError:
            self._log("最大ステップは整数で入力してください")
            return
        title = self._window_var.get().strip() or None
        use_cache = bool(self._use_cache_var.get())

        self._running = True
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._status_var.set("実行中…")

        def _run() -> None:
            try:
                r = runtime.run_task(
                    goal=goal,
                    window_title=title,
                    max_steps=max_steps,
                    use_cache=use_cache,
                )
                msg_ok = f"タスク完了: {r}"
                self.after(0, lambda m=msg_ok: self._log(m))
            except Exception as exc:
                msg_err = f"タスク失敗: {exc}"
                self.after(0, lambda m=msg_err: self._log(m))
            finally:
                self.after(0, self._task_done)

        threading.Thread(target=_run, daemon=True).start()

    def _on_stop(self) -> None:
        if self._addon is None:
            return
        ks = getattr(self._addon, "_kill_switch", None)
        if ks is None:
            self._log("KillSwitch が無効です")
            return
        try:
            ks.set(reason="gui_stop")
            self._log("Stop 要求を送りました（ESC と同等）")
        except Exception as e:
            self._log(f"Stop 失敗: {e}")

    def _task_done(self) -> None:
        self._running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._status_var.set("待機中")
        self._refresh_cache()

    # ──────────────────────────────────────
    # キャッシュ
    # ──────────────────────────────────────

    def _refresh_cache(self) -> None:
        for item in self._cache_tree.get_children():
            self._cache_tree.delete(item)
        runtime = self._get_runtime()
        if runtime is None:
            return
        try:
            entries = runtime._cache.all_entries()  # noqa: SLF001
        except Exception as e:
            self._log(f"キャッシュ取得失敗: {e}")
            return
        now = time.time()
        for e in entries:
            age = max(0, int(now - float(e.get("created_at", now))))
            self._cache_tree.insert(
                "", "end",
                values=(
                    e.get("description", ""),
                    e.get("window_title", ""),
                    f"({e.get('px_x', 0)},{e.get('px_y', 0)})",
                    e.get("hits", 0),
                    age,
                ),
                iid=e.get("key", ""),
            )

    def _invalidate_selected(self) -> None:
        runtime = self._get_runtime()
        if runtime is None:
            return
        selection = self._cache_tree.selection()
        if not selection:
            self._log("破棄する行を選択してください")
            return
        removed = 0
        for key in selection:
            try:
                if runtime._cache.invalidate(key):  # noqa: SLF001
                    removed += 1
            except Exception as e:
                self._log(f"破棄失敗: {e}")
        self._log(f"{removed} 件破棄しました")
        self._refresh_cache()

    def _clear_cache(self) -> None:
        runtime = self._get_runtime()
        if runtime is None:
            return
        try:
            runtime._cache.clear()  # noqa: SLF001
            self._log("キャッシュを全削除しました")
        except Exception as e:
            self._log(f"全削除失敗: {e}")
        self._refresh_cache()

    # ──────────────────────────────────────
    # LED ポーリング
    # ──────────────────────────────────────

    def _schedule_led_poll(self) -> None:
        self._poll_led()

    def _poll_led(self) -> None:
        try:
            ks = getattr(self._addon, "_kill_switch", None) if self._addon else None
            fired = bool(ks.is_set()) if ks is not None else False
            color = "#e74c3c" if fired else "#2ecc71"
            status = "発火" if fired else "待機"
            self._led_canvas.itemconfigure(self._led_oval, fill=color)
            self._led_status_var.set(status)
        except Exception:
            pass
        # 500ms 間隔で継続
        try:
            self.after(500, self._poll_led)
        except tk.TclError:
            # ウィンドウ破棄後
            pass

    # ──────────────────────────────────────
    # ヘルパ
    # ──────────────────────────────────────

    def _get_runtime(self) -> Any | None:
        if self._addon is None:
            return None
        try:
            return self._addon._ensure_runtime()  # noqa: SLF001
        except Exception as e:
            logger.debug("runtime 取得失敗: %s", e)
            return None

    def _log(self, msg: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
