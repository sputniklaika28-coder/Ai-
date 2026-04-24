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

from addons.tactical_exorcist import trpg_data as TD

# 参考キャラクター / 世界観設定ファイル
_CONFIGS_DIR = _PROJECT_ROOT / "configs"
_WORLD_SETTING_PATH = _CONFIGS_DIR / "world_setting_compressed.txt"
_REFERENCE_CHARACTER_PATH = _CONFIGS_DIR / "reference_character.json"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""
    except Exception:
        return ""


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


def _pick_int(status_list: list[dict], label: str, default: int = 0) -> int:
    """CCFolia status 配列から特定ラベルの value を int で拾う。"""
    for entry in status_list or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("label") == label:
            v = entry.get("value", default)
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
    return default


def _pick_param(params_list: list[dict], label: str, default: int = 0) -> int:
    """CCFolia params 配列から特定ラベルの value を int で拾う。"""
    for entry in params_list or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("label") == label:
            try:
                return int(entry.get("value", default))
            except (TypeError, ValueError):
                return default
    return default


def _ccfolia_piece_to_sheet(piece: dict) -> dict | None:
    """CCFolia 形式 ({"kind":"character","data":{...}}) → フラットシート dict。

    AI が組んだ memo/commands/status/params は `_vtt_piece_raw` に保存し、
    ココフォリアへコピーする際はそのまま貼付できるようにする。
    """
    if not isinstance(piece, dict) or piece.get("kind") != "character":
        return None
    data = piece.get("data")
    if not isinstance(data, dict):
        return None

    status = data.get("status") or []
    params = data.get("params") or []
    memo_raw = data.get("memo", "") or ""

    # memo 冒頭の「【二つ名】...」を alias として抜き取る
    alias = ""
    memo = memo_raw
    if memo_raw.startswith("【二つ名】"):
        first_line, sep, rest = memo_raw.partition("\n")
        alias = first_line.replace("【二つ名】", "").strip()
        memo = rest.lstrip("\n") if sep else ""

    sheet = {
        "name": data.get("name", "名無し"),
        "alias": alias,
        "memo": memo,
        "hp": _pick_int(status, "体力", 10),
        "sp": _pick_int(status, "霊力", 10),
        "evasion": _pick_int(status, "回避D", 2),
        "body": _pick_param(params, "体", 3),
        "soul": _pick_param(params, "霊", 3),
        "skill": _pick_param(params, "巧", 3),
        "magic": _pick_param(params, "術", 3),
        "mobility": _pick_param(params, "機動力", 3),
        "armor": _pick_param(params, "装甲", 0),
        "items": {
            "katashiro": _pick_int(status, "形代", 1),
            "haraegushi": _pick_int(status, "祓串", 0),
            "shimenawa": _pick_int(status, "注連鋼縄", 0),
            "juryudan": _pick_int(status, "呪瘤檀", 0),
            "ireikigu": _pick_int(status, "医霊器具", 0),
            "meifuku": _pick_int(status, "名伏", 0),
            "jutsuyen": _pick_int(status, "術延起点", 0),
        },
        "_vtt_piece_raw": piece,
    }
    return sheet


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

        # 装備・所属 (v1.0 仕様)
        self.var_org_display = tk.StringVar(value="")
        self.var_armor_display = tk.StringVar(value="")
        self.var_weapon1_display = tk.StringVar(value="")
        self.var_weapon2_display = tk.StringVar(value="")
        self.var_skill_checks: dict[str, tk.BooleanVar] = {
            k: tk.BooleanVar(value=False) for k in TD.SKILLS
        }
        # Arts は Listbox で複数選択するため BoolVar ではなく選択状態を保持する
        self._selected_art_keys: list[str] = []
        self.var_notes = tk.StringVar(value="")

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
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # タブ1: キャラ設定・メモ
        tab_memo = ttk.Frame(notebook, padding=8)
        notebook.add(tab_memo, text="3. キャラ設定・メモ")
        self.text_memo = scrolledtext.ScrolledText(tab_memo, font=("", 10), wrap=tk.WORD)
        self.text_memo.pack(fill=tk.BOTH, expand=True)

        # タブ2: 装備・所属 (v1.0 仕様)
        tab_equip = ttk.Frame(notebook, padding=8)
        notebook.add(tab_equip, text="装備・所属 (v1.0)")
        self._build_equipment_tab(tab_equip)

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

    # ── 装備・所属タブ (v1.0) ────────────────────────────────────────────────

    def _build_equipment_tab(self, parent: ttk.Frame) -> None:
        """組織・武器・防具・スキル・術の選択 UI と副次ステータス自動算出ボタン。"""
        # 組織
        row = 0
        ttk.Label(parent, text="所属組織:").grid(row=row, column=0, sticky="w", pady=2)
        self.cmb_org = ttk.Combobox(
            parent,
            textvariable=self.var_org_display,
            values=[label for _, label in TD.org_choices()],
            state="readonly",
            width=32,
        )
        self.cmb_org.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        # 防具
        ttk.Label(parent, text="防具:").grid(row=row, column=0, sticky="w", pady=2)
        self.cmb_armor = ttk.Combobox(
            parent,
            textvariable=self.var_armor_display,
            values=[label for _, label in TD.armor_choices()],
            state="readonly",
            width=32,
        )
        self.cmb_armor.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        # 武器 x2
        ttk.Label(parent, text="武器1:").grid(row=row, column=0, sticky="w", pady=2)
        self.cmb_weapon1 = ttk.Combobox(
            parent,
            textvariable=self.var_weapon1_display,
            values=[""] + [label for _, label in TD.weapon_choices()],
            state="readonly",
            width=32,
        )
        self.cmb_weapon1.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        ttk.Label(parent, text="武器2:").grid(row=row, column=0, sticky="w", pady=2)
        self.cmb_weapon2 = ttk.Combobox(
            parent,
            textvariable=self.var_weapon2_display,
            values=[""] + [label for _, label in TD.weapon_choices()],
            state="readonly",
            width=32,
        )
        self.cmb_weapon2.grid(row=row, column=1, sticky="ew", pady=2)
        row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=6
        )
        row += 1

        # スキル (チェックボックス)
        ttk.Label(parent, text="スキル (D7/F):").grid(row=row, column=0, sticky="nw", pady=2)
        f_skills = ttk.Frame(parent)
        f_skills.grid(row=row, column=1, sticky="ew", pady=2)
        for i, (key, label) in enumerate(TD.skill_choices()):
            ttk.Checkbutton(f_skills, text=label, variable=self.var_skill_checks[key]).grid(
                row=i // 2, column=i % 2, sticky="w", padx=4
            )
        row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=6
        )
        row += 1

        # 術 (Listbox 複数選択)
        ttk.Label(parent, text="祓魔術 (D4/I):").grid(row=row, column=0, sticky="nw", pady=2)
        f_arts = ttk.Frame(parent)
        f_arts.grid(row=row, column=1, sticky="ew", pady=2)
        self.lb_arts = tk.Listbox(f_arts, selectmode=tk.MULTIPLE, height=8, exportselection=False)
        self._art_keys_ordered = [k for k, _ in TD.art_choices()]
        for _, label in TD.art_choices():
            self.lb_arts.insert(tk.END, label)
        self.lb_arts.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(f_arts, orient=tk.VERTICAL, command=self.lb_arts.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.lb_arts.config(yscrollcommand=sb.set)
        row += 1

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=6
        )
        row += 1

        ttk.Button(
            parent, text="🧮 副次ステータスを自動算出", command=self._apply_derived_stats
        ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        row += 1

        ttk.Label(
            parent, textvariable=self.var_notes, foreground="gray", wraplength=320, justify="left"
        ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)

        parent.columnconfigure(1, weight=1)

    # ── 表示ラベル ⇄ キー変換 ──────────────────────────────────────────────

    @staticmethod
    def _label_to_key(display: str, choices: list[tuple[str, str]]) -> str | None:
        for k, label in choices:
            if label == display:
                return k
        return None

    @staticmethod
    def _key_to_label(key: str, choices: list[tuple[str, str]]) -> str:
        for k, label in choices:
            if k == key:
                return label
        return ""

    def _selected_skill_keys(self) -> list[str]:
        return [k for k, var in self.var_skill_checks.items() if var.get()]

    def _selected_art_keys_from_ui(self) -> list[str]:
        indices = self.lb_arts.curselection()
        return [self._art_keys_ordered[i] for i in indices]

    def _apply_derived_stats(self) -> None:
        """UI の選択から副次ステータスを算出して各 IntVar に反映する。"""
        org_key = self._label_to_key(self.var_org_display.get(), TD.org_choices())
        armor_key = self._label_to_key(self.var_armor_display.get(), TD.armor_choices())
        skill_keys = self._selected_skill_keys()
        try:
            d = TD.derive_stats(
                body=self.var_body.get(),
                soul=self.var_soul.get(),
                skill=self.var_skill.get(),
                magic=self.var_magic.get(),
                org=org_key,
                armor=armor_key,
                skill_keys=skill_keys,
            )
        except tk.TclError:
            messagebox.showerror("エラー", "B/R/K/A は整数で入力してください。")
            return
        self.var_hp.set(d.hp)
        self.var_sp.set(d.mp)
        self.var_mobility.set(d.mv)
        self.var_evasion.set(d.ed)
        self.var_armor.set(d.arm)
        self.var_notes.set(" / ".join(d.notes) if d.notes else "算出完了")
        self.status_var.set(f"✓ 副次ステータス算出: HP{d.hp} MP{d.mp} MV{d.mv} ED{d.ed} ARM{d.arm}")

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

        # v1.0 仕様の所属・装備・スキル・術キー
        org_key = self._label_to_key(self.var_org_display.get(), TD.org_choices())
        armor_key = self._label_to_key(self.var_armor_display.get(), TD.armor_choices())
        weapon_keys = [
            self._label_to_key(self.var_weapon1_display.get(), TD.weapon_choices()),
            self._label_to_key(self.var_weapon2_display.get(), TD.weapon_choices()),
        ]
        data["trpg_v1"] = {
            "org": org_key,
            "armor": armor_key,
            "weapons": [k for k in weapon_keys if k],
            "skill_keys": self._selected_skill_keys(),
            "art_keys": self._selected_art_keys_from_ui(),
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

        # v1.0 仕様: 所属・装備・スキル・術の復元
        trpg_v1 = data.get("trpg_v1", {})
        self.var_org_display.set(self._key_to_label(trpg_v1.get("org") or "", TD.org_choices()))
        self.var_armor_display.set(
            self._key_to_label(trpg_v1.get("armor") or "", TD.armor_choices())
        )
        weapons = trpg_v1.get("weapons", [])
        self.var_weapon1_display.set(
            self._key_to_label(weapons[0], TD.weapon_choices()) if len(weapons) > 0 else ""
        )
        self.var_weapon2_display.set(
            self._key_to_label(weapons[1], TD.weapon_choices()) if len(weapons) > 1 else ""
        )
        skill_keys = set(trpg_v1.get("skill_keys", []))
        for k, var in self.var_skill_checks.items():
            var.set(k in skill_keys)
        art_keys = set(trpg_v1.get("art_keys", []))
        if hasattr(self, "lb_arts"):
            self.lb_arts.selection_clear(0, tk.END)
            for i, k in enumerate(self._art_keys_ordered):
                if k in art_keys:
                    self.lb_arts.selection_set(i)
        self.var_notes.set("")

    # ── AI生成 ────────────────────────────────────────────────────────────────

    def _build_char_prompt(self, user_req: str) -> tuple[str, str]:
        """ルール本文と参考キャラを丸ごと注入した (system, user) を返す。

        qwen3 で実証された「ルール全文 + 参考 1 枚 → 完成 CCFolia JSON」方式。
        """
        world_setting = _read_text(_WORLD_SETTING_PATH)
        reference = _read_text(_REFERENCE_CHARACTER_PATH)

        system = (
            "あなたはTRPG『タクティカル祓魔師』の熟練プレイヤー兼データジェネレーターです。\n"
            "以下のルールブック本文を厳密に遵守し、ユーザー要望に合わせた "
            "キャラクターデータを CCFolia 貼付用 JSON として生成してください。\n"
            "\n"
            "【絶対順守】\n"
            "- 出力は `{\"kind\":\"character\",\"data\":{...}}` の JSON オブジェクト 1 個のみ。\n"
            "- `data` には name, initiative, memo, commands, status, params を必ず含める。\n"
            "- memo と commands は参考シートと同等の厚みでチャットパレット/判定式/装備説明を書く。\n"
            "- status は体力/霊力/回避D+支給装備、params は体/霊/巧/術/機動力/装甲 を含める。\n"
            "- 副次ステータス (HP=B, MP=R, MV=ceil(max(B,K)/2)+組織補正, "
            "ED=max(B,R,K)+防具ED+組織補正, ARM=防具ARM) は本文に従って計算する。\n"
            "- 余計な前置き・思考・マークダウンコードブロックは絶対に書かない。\n"
            "- JSON 以外の文字を 1 字も出力しないこと。\n"
        )
        if world_setting:
            system += (
                "\n========== ルールブック本文 ==========\n"
                f"{world_setting}\n"
                "========== ルールブック本文 ここまで ==========\n"
            )
        if reference:
            system += (
                "\n========== 参考キャラクター（出力フォーマット例） ==========\n"
                f"{reference}\n"
                "========== 参考キャラクター ここまで ==========\n"
                "上記と同じ JSON 構造・memo/commands の粒度で出力すること。\n"
            )

        user = (
            f"ユーザー要望: {user_req}\n\n"
            "この要望を満たすタクティカル祓魔師 PC を 1 人作成し、"
            "CCFolia 貼付用 JSON だけを出力してください。"
        )
        return system, user

    def _start_generate(self) -> None:
        if not self.lm_client.is_server_running_sync():
            messagebox.showerror("エラー", "LM-Studioが起動していません。")
            return
        self.btn_gen.config(state="disabled")
        self.status_var.set("生成中...お待ちください")

        def run() -> None:
            user_req = self.text_input.get("1.0", tk.END).strip()
            sys_prompt, user_msg = self._build_char_prompt(user_req)
            try:
                content, _tool_calls = self.lm_client.generate_response_sync(
                    system_prompt=sys_prompt,
                    user_message=user_msg,
                    temperature=0.7,
                    max_tokens=4096,
                    timeout=None,
                )
            except Exception as e:
                print(f"生成エラー: {e}")
                content = None
            self.after(0, self._on_generate_finish, content)

        threading.Thread(target=run, daemon=True).start()

    def _on_generate_finish(self, result: str | None) -> None:
        self.btn_gen.config(state="normal")
        if not result:
            self.status_var.set("❌ 生成失敗")
            return
        try:
            data = json.loads(result)
        except Exception as e:
            self.status_var.set("❌ JSONパースエラー")
            print(f"JSONパース失敗: {e}\nraw: {result[:500]}")
            return

        sheet = _ccfolia_piece_to_sheet(data)
        if sheet is None:
            self.status_var.set("❌ 生成結果の形式が不正です")
            return
        self._apply_json_to_ui(sheet)
        self.status_var.set("✓ 生成完了！そのままコピーできます")

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
        # addon.py の build_ccfolia_piece_from_sheet に委譲する。
        # _vtt_piece_raw が残っていれば AI が組んだ memo/commands/status/params を
        # そのまま返し、そうでなければ flat フィールドから構築する。
        from addons.tactical_exorcist.addon import build_ccfolia_piece_from_sheet

        sheet = self._current_sheet_dict()
        ccfolia_data = build_ccfolia_piece_from_sheet(sheet)

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
