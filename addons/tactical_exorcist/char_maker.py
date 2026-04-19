# ================================
# ファイル: addons/tactical_exorcist/char_maker.py
# タクティカル祓魔師専用 キャラクター自動生成＆CCFolia出力アプリ
#
# このファイルは tactical_exorcist アドオンの付属機能です。
# スタンドアロンとして直接起動することもできます:
#   python addons/tactical_exorcist/char_maker.py
# ================================

from __future__ import annotations

import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# ── パス解決 ──────────────────────────────────────────────────────────────────
# このファイルは addons/tactical_exorcist/ にあるため、
# プロジェクトルートは2階層上になる
_ADDON_DIR = Path(__file__).resolve().parent        # addons/tactical_exorcist/
_PROJECT_ROOT = _ADDON_DIR.parent.parent             # プロジェクトルート

# キャラクター保存先: configs/saved_pcs/ (core/char_maker.py と共通)
SAVED_PCS_DIR = _PROJECT_ROOT / "configs" / "saved_pcs"
SAVED_PCS_DIR.mkdir(parents=True, exist_ok=True)

# core モジュールをパスに追加
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from core.lm_client import LMClient
except ImportError:
    print("❌ エラー: core/lm_client.py が見つかりません。プロジェクトルートから起動してください。")
    sys.exit(1)


# ── ユーティリティ ────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8").strip()
            return json.loads(content) if content else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── キャラクターメーカー GUI ──────────────────────────────────────────────────

class TacticalExorcistCharMaker(tk.Tk):
    """タクティカル祓魔師専用キャラクタージェネレーター。

    core/char_maker.py と同等機能をアドオンパッケージ内に収録したもの。
    キャラクターデータは configs/saved_pcs/ に保存し、
    core 版と互換性を保つ。
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("タクティカル祓魔師 - キャラクタージェネレーター")
        self.geometry("950x650")

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.lm_client = LMClient()
        self._last_json_raw: dict = {}

        self._init_vars()
        self._build_ui()
        self._refresh_saved_list()

    # ── 変数初期化 ────────────────────────────────────────────────────────────

    def _init_vars(self) -> None:
        self.var_name = tk.StringVar(value="名無し")
        self.var_alias = tk.StringVar(value="")

        # ステータス
        self.var_hp = tk.IntVar(value=10)
        self.var_sp = tk.IntVar(value=10)
        self.var_evasion = tk.IntVar(value=2)
        self.var_mobility = tk.IntVar(value=2)
        self.var_armor = tk.IntVar(value=0)

        # パラメータ
        self.var_body = tk.IntVar(value=3)
        self.var_soul = tk.IntVar(value=3)
        self.var_skill = tk.IntVar(value=3)
        self.var_magic = tk.IntVar(value=3)

        # アイテム
        self.var_katashiro = tk.IntVar(value=1)
        self.var_haraegushi = tk.IntVar(value=0)
        self.var_shimenawa = tk.IntVar(value=0)
        self.var_juryudan = tk.IntVar(value=0)
        self.var_ireikigu = tk.IntVar(value=0)
        self.var_meifuku = tk.IntVar(value=0)
        self.var_jutsuyen = tk.IntVar(value=0)

    # ── UI 構築 ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 左ペイン：AI生成＆保存リスト
        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        self._build_left(left)

        # 中央ペイン：エディタ
        mid = ttk.Frame(paned)
        paned.add(mid, weight=2)
        self._build_mid(mid)

        # 右ペイン：設定・出力
        right = ttk.Frame(paned)
        paned.add(right, weight=2)
        self._build_right(right)

    def _build_left(self, parent: ttk.Frame) -> None:
        f_ai = ttk.LabelFrame(parent, text="1. AIに自動作成させる", padding=8)
        f_ai.pack(fill=tk.X, pady=(0, 10))

        self.text_input = scrolledtext.ScrolledText(f_ai, width=20, height=4, font=("", 10))
        self.text_input.pack(fill=tk.X, pady=(0, 5))
        self.text_input.insert("1.0", "例：射撃戦が得意な少女祓魔師。")

        self.btn_gen = ttk.Button(f_ai, text="✨ AIで生成", command=self._start_generate)
        self.btn_gen.pack(fill=tk.X)

        f_list = ttk.LabelFrame(parent, text="保存済みキャラクター", padding=8)
        f_list.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(f_list, font=("", 11))
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        btn_frame = ttk.Frame(f_list)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="読込", command=self._load_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=1
        )
        ttk.Button(btn_frame, text="削除", command=self._delete_selected).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=1
        )

    def _build_mid(self, parent: ttk.Frame) -> None:
        f_basic = ttk.LabelFrame(parent, text="2. ステータス・アイテム調整", padding=8)
        f_basic.pack(fill=tk.BOTH, expand=True)

        def row(label: str, var: tk.Variable, r: int, c: int, w: int = 5) -> None:
            ttk.Label(f_basic, text=label).grid(row=r, column=c, sticky="w", padx=4, pady=2)
            ttk.Entry(f_basic, textvariable=var, width=w).grid(
                row=r, column=c + 1, sticky="w", padx=4, pady=2
            )

        def sep(r: int) -> None:
            ttk.Separator(f_basic, orient=tk.HORIZONTAL).grid(
                row=r, column=0, columnspan=4, sticky="ew", pady=6
            )

        row("名前:", self.var_name, 0, 0, 15)
        row("二つ名:", self.var_alias, 0, 2, 15)
        sep(1)
        row("体力(HP):", self.var_hp, 2, 0)
        row("霊力(SP):", self.var_sp, 2, 2)
        row("回避D:", self.var_evasion, 3, 0)
        row("機動力:", self.var_mobility, 3, 2)
        row("装甲:", self.var_armor, 4, 0)
        sep(5)
        row("体:", self.var_body, 6, 0)
        row("霊:", self.var_soul, 6, 2)
        row("巧:", self.var_skill, 7, 0)
        row("術:", self.var_magic, 7, 2)
        sep(8)
        row("形代:", self.var_katashiro, 9, 0)
        row("祓串:", self.var_haraegushi, 9, 2)
        row("注連鋼縄:", self.var_shimenawa, 10, 0)
        row("呪瘤檀:", self.var_juryudan, 10, 2)
        row("医霊器具:", self.var_ireikigu, 11, 0)
        row("名伏:", self.var_meifuku, 11, 2)
        row("術延起点:", self.var_jutsuyen, 12, 0)

    def _build_right(self, parent: ttk.Frame) -> None:
        f_memo = ttk.LabelFrame(parent, text="3. キャラ設定・メモ", padding=8)
        f_memo.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.text_memo = scrolledtext.ScrolledText(f_memo, font=("", 10), wrap=tk.WORD)
        self.text_memo.pack(fill=tk.BOTH, expand=True)

        f_out = ttk.LabelFrame(parent, text="4. 保存と出力", padding=8)
        f_out.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="ステータスを調整して出力してください")
        ttk.Label(
            f_out, textvariable=self.status_var, foreground="blue", font=("", 9, "bold")
        ).pack(pady=(0, 4))

        ttk.Button(f_out, text="💾 このキャラを保存する", command=self._save_character).pack(
            fill=tk.X, pady=2
        )
        ttk.Button(
            f_out, text="📋 ココフォリア用コマとしてコピー", command=self._copy_ccfolia
        ).pack(fill=tk.X, pady=2)

    # ── キャラクター I/O ──────────────────────────────────────────────────────

    def _refresh_saved_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for f in sorted(SAVED_PCS_DIR.glob("*.json")):
            self.listbox.insert(tk.END, f.stem)

    def _get_selected_file(self) -> Path | None:
        idx = self.listbox.curselection()
        if not idx:
            return None
        return SAVED_PCS_DIR / f"{self.listbox.get(idx[0])}.json"

    def _save_character(self) -> None:
        name = self.var_name.get().strip()
        if not name:
            messagebox.showerror("エラー", "名前を入力してください。")
            return

        data = dict(self._last_json_raw)
        data.update(
            {
                "name": name,
                "alias": self.var_alias.get(),
                "hp": self.var_hp.get(),
                "sp": self.var_sp.get(),
                "evasion": self.var_evasion.get(),
                "mobility": self.var_mobility.get(),
                "armor": self.var_armor.get(),
                "body": self.var_body.get(),
                "soul": self.var_soul.get(),
                "skill": self.var_skill.get(),
                "magic": self.var_magic.get(),
                "memo": self.text_memo.get("1.0", tk.END).strip(),
            }
        )
        data["items"] = {
            "katashiro": self.var_katashiro.get(),
            "haraegushi": self.var_haraegushi.get(),
            "shimenawa": self.var_shimenawa.get(),
            "juryudan": self.var_juryudan.get(),
            "ireikigu": self.var_ireikigu.get(),
            "meifuku": self.var_meifuku.get(),
            "jutsuyen": self.var_jutsuyen.get(),
        }

        _save_json(SAVED_PCS_DIR / f"{name}.json", data)
        self.status_var.set(f"✓ {name} を保存しました！")
        self._refresh_saved_list()

    def _load_selected(self) -> None:
        file_path = self._get_selected_file()
        if not file_path:
            return
        data = _load_json(file_path)
        self._apply_json_to_ui(data)
        self.status_var.set(f"✓ {data.get('name', 'キャラ')} を読み込みました")

    def _delete_selected(self) -> None:
        file_path = self._get_selected_file()
        if not file_path:
            return
        if messagebox.askyesno("削除確認", f"{file_path.stem} を削除しますか？"):
            file_path.unlink(missing_ok=True)
            self._refresh_saved_list()

    def _apply_json_to_ui(self, data: dict) -> None:
        self._last_json_raw = data
        self.var_name.set(data.get("name", "名無し"))
        self.var_alias.set(data.get("alias", ""))
        self.var_hp.set(data.get("hp", 10))
        self.var_sp.set(data.get("sp", 10))
        self.var_evasion.set(data.get("evasion", 2))
        self.var_mobility.set(data.get("mobility", 2))
        self.var_armor.set(data.get("armor", 0))
        self.var_body.set(data.get("body", 3))
        self.var_soul.set(data.get("soul", 3))
        self.var_skill.set(data.get("skill", 3))
        self.var_magic.set(data.get("magic", 3))

        items = data.get("items", {})
        self.var_katashiro.set(items.get("katashiro", 1))
        self.var_haraegushi.set(items.get("haraegushi", 0))
        self.var_shimenawa.set(items.get("shimenawa", 0))
        self.var_juryudan.set(items.get("juryudan", 0))
        self.var_ireikigu.set(items.get("ireikigu", 0))
        self.var_meifuku.set(items.get("meifuku", 0))
        self.var_jutsuyen.set(items.get("jutsuyen", 0))

        self.text_memo.delete("1.0", tk.END)
        self.text_memo.insert("1.0", data.get("memo", ""))

    # ── AI生成 ────────────────────────────────────────────────────────────────

    def _build_char_prompt(self, user_req: str) -> str:
        return f"""
あなたはTRPG『タクティカル祓魔師』のプレイヤーです。
ユーザーの要望に合わせて、以下のJSONフォーマットの空欄を論理的に埋めてください。
武器や特技などはユーザーの要望に合わせて複数個作成してください。
【重要】必ず有効なJSON形式のみを出力し、Markdownコードブロック(```json)などは使用しないでください。

ユーザー要望: {user_req}

{{
  "name": "キャラクターの名前", "alias": "二つ名",
  "hp": 15, "sp": 15, "evasion": 2, "mobility": 3, "armor": 0,
  "body": 3, "soul": 3, "skill": 3, "magic": 3,
  "items": {{"katashiro": 1, "haraegushi": 0, "shimenawa": 0, "juryudan": 0, "ireikigu": 0, "meifuku": 0, "jutsuyen": 0}},
  "memo": "キャラクターの背景や性格",
  "skills": [
    {{"name": "戦術機動", "description": "手番開始時に使用可能。『難易度:NORMAL』で【巧】判定を行う。成功した場合、即座に回避ダイスを2つ獲得し、更にその手番中は最大で【機動力】の2倍のマスを移動できる。 ただし、手番中に行う能動的な行動の判定の難易度が1段階上昇する。【巧】判定に失敗した場合、回避ダイスの獲得と移動距離の増加は行われず、判定の難易度上昇だけを被る。"}}
  ],
  "weapons": [
    {{"name": "大型遠隔祭具", "description": "【巧】の値を参照して「遠隔攻撃」を行い、攻撃成功時、「5」点の物理ダメージを与える。"}}
  ]
}}
"""

    def _start_generate(self) -> None:
        if not self.lm_client.is_server_running():
            messagebox.showerror("エラー", "LM-Studioが起動していません。")
            return
        self.btn_gen.config(state="disabled")
        self.status_var.set("生成中...お待ちください")

        def run() -> None:
            user_req = self.text_input.get("1.0", tk.END).strip()
            sys_prompt = "あなたはデータジェネレーターです。必ず指定されたJSON形式のみを出力し、余計な会話はしないでください。"
            result = self.lm_client.generate_response(
                system_prompt=sys_prompt,
                user_message=self._build_char_prompt(user_req),
                temperature=0.7,
                max_tokens=1500,
                timeout=None,
            )
            self.after(0, self._on_generate_finish, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_generate_finish(self, result: str) -> None:
        self.btn_gen.config(state="normal")
        if not result:
            self.status_var.set("❌ 生成失敗")
            return
        clean = result.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(clean)
            self._apply_json_to_ui(data)
            self.status_var.set("✓ 生成完了！内容を調整してください")
        except Exception as e:
            self.status_var.set("❌ JSONパースエラー")
            print(f"エラー詳細: {e}")

    # ── CCFolia 出力 ──────────────────────────────────────────────────────────

    def _current_sheet_dict(self) -> dict:
        """UI 上の入力値からシート dict を組み立てる。"""
        sheet = dict(self._last_json_raw or {})
        sheet.update({
            "name": self.var_name.get(),
            "alias": self.var_alias.get(),
            "hp": self.var_hp.get(), "sp": self.var_sp.get(),
            "evasion": self.var_evasion.get(),
            "mobility": self.var_mobility.get(),
            "armor": self.var_armor.get(),
            "body": self.var_body.get(), "soul": self.var_soul.get(),
            "skill": self.var_skill.get(), "magic": self.var_magic.get(),
            "items": {
                "katashiro": self.var_katashiro.get(),
                "haraegushi": self.var_haraegushi.get(),
                "shimenawa": self.var_shimenawa.get(),
                "juryudan": self.var_juryudan.get(),
                "ireikigu": self.var_ireikigu.get(),
                "meifuku": self.var_meifuku.get(),
                "jutsuyen": self.var_jutsuyen.get(),
            },
            "memo": self.text_memo.get("1.0", tk.END).strip(),
        })
        return sheet

    def _copy_ccfolia(self) -> None:
        # addon.py 内の汎用ロジックに委譲する（重複実装を排除）
        from addons.tactical_exorcist.addon import _build_ccfolia_commands

        sheet = self._current_sheet_dict()
        name = sheet.get("name") or "名無し"
        memo_text = f"【二つ名】{sheet.get('alias', '')}\n\n{sheet.get('memo', '')}"

        items = sheet.get("items", {})
        status = [
            {"label": "体力", "value": sheet["hp"], "max": sheet["hp"]},
            {"label": "霊力", "value": sheet["sp"], "max": sheet["sp"]},
            {"label": "回避D", "value": sheet["evasion"], "max": sheet["evasion"]},
            {"label": "形代", "value": items.get("katashiro", 0), "max": items.get("katashiro", 0)},
            {"label": "祓串", "value": items.get("haraegushi", 0), "max": items.get("haraegushi", 0)},
            {"label": "注連鋼縄", "value": items.get("shimenawa", 0), "max": items.get("shimenawa", 0)},
            {"label": "呪瘤檀", "value": items.get("juryudan", 0), "max": items.get("juryudan", 0)},
            {"label": "医霊器具", "value": items.get("ireikigu", 0), "max": items.get("ireikigu", 0)},
            {"label": "名伏", "value": items.get("meifuku", 0), "max": items.get("meifuku", 0)},
            {"label": "術延起点", "value": items.get("jutsuyen", 0), "max": items.get("jutsuyen", 0)},
        ]
        params = [
            {"label": "体", "value": str(sheet["body"])},
            {"label": "霊", "value": str(sheet["soul"])},
            {"label": "巧", "value": str(sheet["skill"])},
            {"label": "術", "value": str(sheet["magic"])},
            {"label": "機動力", "value": str(sheet["mobility"])},
            {"label": "装甲", "value": str(sheet["armor"])},
        ]
        ccfolia_data = {
            "kind": "character",
            "data": {
                "name": name,
                "initiative": 0,
                "memo": memo_text,
                "commands": _build_ccfolia_commands(sheet),
                "status": status,
                "params": params,
            },
        }

        self.clipboard_clear()
        self.clipboard_append(json.dumps(ccfolia_data, ensure_ascii=False))
        self.update()

        self.status_var.set("✓ ココフォリア用にコピー！Ctrl+Vで貼り付け")
        messagebox.showinfo(
            "コピー完了",
            "ココフォリア用のクリップボードデータをコピーしました！\n\n"
            "ココフォリアの画面を開いて Ctrl+V (貼り付け) を押すだけで、\n"
            "見やすいチャットパレット付きの駒が生成されます。",
        )


# ── エントリポイント ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TacticalExorcistCharMaker()
    app.mainloop()
