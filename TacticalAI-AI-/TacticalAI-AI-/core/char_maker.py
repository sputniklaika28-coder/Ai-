# ================================
# ファイル: core/char_maker.py
# タクティカル祓魔師 - キャラクター自動生成＆CCFolia出力専用アプリ
# ================================

import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

# --- パス設定 ---
_THIS = Path(__file__).resolve()
if _THIS.parent.name == "core":
    BASE_DIR = _THIS.parent.parent
else:
    BASE_DIR = _THIS.parent

CONFIGS_DIR = BASE_DIR / "configs"
SAVED_PCS_DIR = CONFIGS_DIR / "saved_pcs"
SAVED_PCS_DIR.mkdir(parents=True, exist_ok=True)

# lm_client の読み込み
sys.path.insert(0, str(BASE_DIR / "core"))
try:
    from lm_client import LMClient
except ImportError:
    print("❌ エラー: core/lm_client.py が見つかりません。")
    sys.exit(1)


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else {}
        except json.JSONDecodeError:
            return {}
    return {}


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class VTTCharMakerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("タクティカル祓魔師 - キャラクタージェネレーター")
        self.geometry("950x650")

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.lm_client = LMClient()
        self._last_json_raw = {}

        self._init_vars()
        self._build_ui()
        self._refresh_saved_list()

    def _init_vars(self):
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

    def _build_ui(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- 左ペイン：AI生成＆保存リスト ---
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        f_ai = ttk.LabelFrame(left, text="1. AIに自動作成させる", padding=8)
        f_ai.pack(fill=tk.X, pady=(0, 10))
        self.text_input = scrolledtext.ScrolledText(f_ai, width=20, height=4, font=("", 10))
        self.text_input.pack(fill=tk.X, pady=(0, 5))
        self.text_input.insert("1.0", "例：射撃戦が得意な少女祓魔師。")
        self.btn_gen = ttk.Button(f_ai, text="✨ AIで生成", command=self._start_generate)
        self.btn_gen.pack(fill=tk.X)

        f_list = ttk.LabelFrame(left, text="保存済みキャラクター", padding=8)
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

        # --- 中央ペイン：エディタ ---
        mid = ttk.Frame(paned)
        paned.add(mid, weight=2)

        f_basic = ttk.LabelFrame(mid, text="2. ステータス・アイテム調整", padding=8)
        f_basic.pack(fill=tk.BOTH, expand=True)

        def make_entry(parent, label, var, r, c, w=5):
            ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=4, pady=2)
            ttk.Entry(parent, textvariable=var, width=w).grid(
                row=r, column=c + 1, sticky="w", padx=4, pady=2
            )

        make_entry(f_basic, "名前:", self.var_name, 0, 0, 15)
        make_entry(f_basic, "二つ名:", self.var_alias, 0, 2, 15)

        ttk.Separator(f_basic, orient=tk.HORIZONTAL).grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=6
        )

        make_entry(f_basic, "体力(HP):", self.var_hp, 2, 0)
        make_entry(f_basic, "霊力(SP):", self.var_sp, 2, 2)
        make_entry(f_basic, "回避D:", self.var_evasion, 3, 0)
        make_entry(f_basic, "機動力:", self.var_mobility, 3, 2)
        make_entry(f_basic, "装甲:", self.var_armor, 4, 0)

        ttk.Separator(f_basic, orient=tk.HORIZONTAL).grid(
            row=5, column=0, columnspan=4, sticky="ew", pady=6
        )

        make_entry(f_basic, "体:", self.var_body, 6, 0)
        make_entry(f_basic, "霊:", self.var_soul, 6, 2)
        make_entry(f_basic, "巧:", self.var_skill, 7, 0)
        make_entry(f_basic, "術:", self.var_magic, 7, 2)

        ttk.Separator(f_basic, orient=tk.HORIZONTAL).grid(
            row=8, column=0, columnspan=4, sticky="ew", pady=6
        )

        make_entry(f_basic, "形代:", self.var_katashiro, 9, 0)
        make_entry(f_basic, "祓串:", self.var_haraegushi, 9, 2)
        make_entry(f_basic, "注連鋼縄:", self.var_shimenawa, 10, 0)
        make_entry(f_basic, "呪瘤檀:", self.var_juryudan, 10, 2)
        make_entry(f_basic, "医霊器具:", self.var_ireikigu, 11, 0)
        make_entry(f_basic, "名伏:", self.var_meifuku, 11, 2)
        make_entry(f_basic, "術延起点:", self.var_jutsuyen, 12, 0)

        # --- 右ペイン：設定・出力 ---
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        f_memo = ttk.LabelFrame(right, text="3. キャラ設定・メモ", padding=8)
        f_memo.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.text_memo = scrolledtext.ScrolledText(f_memo, font=("", 10), wrap=tk.WORD)
        self.text_memo.pack(fill=tk.BOTH, expand=True)

        f_out = ttk.LabelFrame(right, text="4. 保存と出力", padding=8)
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

    def _refresh_saved_list(self):
        self.listbox.delete(0, tk.END)
        for f in sorted(SAVED_PCS_DIR.glob("*.json")):
            self.listbox.insert(tk.END, f.stem)

    def _get_selected_file(self):
        idx = self.listbox.curselection()
        if not idx:
            return None
        return SAVED_PCS_DIR / f"{self.listbox.get(idx[0])}.json"

    def _save_character(self):
        name = self.var_name.get().strip()
        if not name:
            messagebox.showerror("エラー", "名前を入力してください。")
            return

        data = self._last_json_raw or {}
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

        save_json(SAVED_PCS_DIR / f"{name}.json", data)
        self.status_var.set(f"✓ {name} を保存しました！")
        self._refresh_saved_list()

    def _load_selected(self):
        file_path = self._get_selected_file()
        if not file_path:
            return
        data = load_json(file_path)
        self._apply_json_to_ui(data)
        self.status_var.set(f"✓ {data.get('name', 'キャラ')} を読み込みました")

    def _delete_selected(self):
        file_path = self._get_selected_file()
        if not file_path:
            return
        if messagebox.askyesno("削除確認", f"{file_path.stem} を削除しますか？"):
            file_path.unlink(missing_ok=True)
            self._refresh_saved_list()

    def _apply_json_to_ui(self, data: dict):
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

    def _start_generate(self):
        if not self.lm_client.is_server_running():
            messagebox.showerror("エラー", "LM-Studioが起動していません。")
            return
        self.btn_gen.config(state="disabled")
        self.status_var.set("生成中...お待ちください")

        def run():
            user_req = self.text_input.get("1.0", tk.END).strip()
            sys_prompt = "あなたはデータジェネレーターです。必ず指定されたJSON形式のみを出力し、余計な会話はしないでください。"
            user_msg = self._build_char_prompt(user_req)
            result = self.lm_client.generate_response(
                system_prompt=sys_prompt,
                user_message=user_msg,
                temperature=0.7,
                max_tokens=1500,
                timeout=None,
            )
            self.after(0, self._on_finish, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_finish(self, result: str):
        self.btn_gen.config(state="normal")
        if not result:
            self.status_var.set("❌ 生成失敗")
            return

        clean = result.replace("```json", "").replace("```", "").strip()
        try:
            # 文字列の場合は dict に変換
            data = json.loads(clean)
            self._apply_json_to_ui(data)
            self.status_var.set("✓ 生成完了！内容を調整してください")
        except Exception as e:
            self.status_var.set("❌ JSONパースエラー")
            print(f"エラー詳細: {e}")

    def _copy_ccfolia(self):
        name = self.var_name.get()
        memo_text = (
            f"【二つ名】{self.var_alias.get()}\n\n{self.text_memo.get('1.0', tk.END).strip()}"
        )

        commands = "◆能力値を使った判定◆\n"
        commands += "{体}b6=>4  //【体】判定\n"
        commands += "{霊}b6=>4  //【霊】判定\n"
        commands += "{巧}b6=>4  //【巧】判定\n"
        commands += "{術}b6=>4  //【術】判定\n\n"

        commands += "◆戦闘中用の判定◆\n"
        commands += "{巧}b6=>4  //戦術機動\n"
        commands += "({体})b6=>4  //近接攻撃\n"
        commands += "({巧})b6=>4  //遠隔攻撃\n"
        commands += "({霊})b6=>4  //霊的攻撃\n"
        commands += "({術})b6=>4  //術発動\n\n"

        commands += "2d6  //ダメージ\n"
        commands += "1d3  //霊的ダメージ\n"
        commands += "b6=>4  //回避判定\n\n"

        commands += "C({体力})  //残り体力\n"
        commands += "C({霊力})  //残り霊力\n\n"

        commands += "◆支給装備◆\n"
        commands += "【形代】：キャラクターが「死亡」した時、①【形代】を1つ消費することで「死亡」を回避する②【体力】【霊力】を半分まで回復した状態でマップ上の「リスポーン地点」にキャラクターを戻す。　また、手番中に好きなタイミングで【形代】を1つ消費することで、キャラクターは【霊力】を2点回復することができる。\n\n"
        commands += "【祓串】：1つ消費することで自身を中心とした7*7マスのどこかに配置するか、近接攻撃または遠隔攻撃に使用できる。近接攻撃に使用した場合は1d6点、遠隔攻撃に使用した場合は3点の「物理ダメージ」を与える。\n\n"
        commands += "【注連鋼縄】：3つ消費することで、【巧】の値を参照してマップ上に設置する。結界に関するルールは2-7：結界の設置についてを参照。\n\n"
        commands += "【呪瘤檀】：攻撃の代わりにこのアイテムを使用する。自分を中心とした5＊5マスのいずれかのマス1つを「中心」に定め、「中心」と隣接する3＊3のマスにいるキャラクター全員に2点の霊的ダメージを与える（回避は『難易度：NORMAL』）。\n\n"

        commands += "◆特技◆\n"
        for skill in self._last_json_raw.get("skills", []):
            commands += f"【{skill.get('name', '')}】：{skill.get('description', '')}\n\n"

        commands += "◆攻撃祭具◆\n"
        for weapon in self._last_json_raw.get("weapons", []):
            commands += f"【{weapon.get('name', '')}】：{weapon.get('description', '')}\n\n"

        commands += "[Credit: 非公式タクティカル祓魔師キャラクターシートVer0.8 著作者様]"

        ccfolia_data = {
            "kind": "character",
            "data": {
                "name": name,
                "initiative": 0,
                "memo": memo_text,
                "commands": commands,
                "status": [
                    {"label": "体力", "value": self.var_hp.get(), "max": self.var_hp.get()},
                    {"label": "霊力", "value": self.var_sp.get(), "max": self.var_sp.get()},
                    {
                        "label": "回避D",
                        "value": self.var_evasion.get(),
                        "max": self.var_evasion.get(),
                    },
                    {
                        "label": "形代",
                        "value": self.var_katashiro.get(),
                        "max": self.var_katashiro.get(),
                    },
                    {
                        "label": "祓串",
                        "value": self.var_haraegushi.get(),
                        "max": self.var_haraegushi.get(),
                    },
                    {
                        "label": "注連鋼縄",
                        "value": self.var_shimenawa.get(),
                        "max": self.var_shimenawa.get(),
                    },
                    {
                        "label": "呪瘤檀",
                        "value": self.var_juryudan.get(),
                        "max": self.var_juryudan.get(),
                    },
                    {
                        "label": "医霊器具",
                        "value": self.var_ireikigu.get(),
                        "max": self.var_ireikigu.get(),
                    },
                    {
                        "label": "名伏",
                        "value": self.var_meifuku.get(),
                        "max": self.var_meifuku.get(),
                    },
                    {
                        "label": "術延起点",
                        "value": self.var_jutsuyen.get(),
                        "max": self.var_jutsuyen.get(),
                    },
                ],
                "params": [
                    {"label": "体", "value": str(self.var_body.get())},
                    {"label": "霊", "value": str(self.var_soul.get())},
                    {"label": "巧", "value": str(self.var_skill.get())},
                    {"label": "術", "value": str(self.var_magic.get())},
                    {"label": "機動力", "value": str(self.var_mobility.get())},
                    {"label": "装甲", "value": str(self.var_armor.get())},
                ],
            },
        }

        # pyperclipへの依存を削除し、Tkinter内蔵のクリップボード機能を使用
        self.clipboard_clear()
        self.clipboard_append(json.dumps(ccfolia_data, ensure_ascii=False))
        self.update()  # クリップボードへの反映を確実にする

        self.status_var.set("✓ ココフォリア用にコピー！Ctrl+Vで貼り付け")
        messagebox.showinfo(
            "コピー完了",
            "ココフォリア用のクリップボードデータをコピーしました！\n\nココフォリアの画面を開いて Ctrl+V (貼り付け) を押すだけで、見やすいチャットパレット付きの駒が生成されます。",
        )


if __name__ == "__main__":
    app = VTTCharMakerApp()
    app.mainloop()
