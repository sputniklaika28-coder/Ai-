# ================================
# ファイル: addons/tactical_exorcist/trpg_data.py
# タクティカル祓魔師 TRPG システム定義 (world_setting_compressed.txt v1.0 準拠)
#
# キャラクターメーカーから参照される静的データと、ステータス算出の
# ユーティリティをまとめる。JS リファレンス実装 (DATA / Character) を
# Python に移植したもの。
# ================================

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any


# ── 組織 [L] ──────────────────────────────────────────────────────────────────

ORGS: dict[str, dict[str, Any]] = {
    "MOE": {"name": "境界対策課", "hp": 0, "mp": 0, "mv": 0, "skill_max": "K+1",
            "auto": ["境対戦闘訓練", "戦術判断"], "resource": "tacticalDice"},
    "KKR": {"name": "結界管理課", "hp": 0, "mp": 0, "mv": 0, "skill_max": "K+1",
            "auto": ["遠隔設置", "結界巧者"], "seal_effects": ["防御結界", "穢装崩壊"]},
    "SAK": {"name": "祭具開発課", "hp": 0, "mp": 0, "mv": 0, "skill_max": "K+1",
            "auto": ["試作カスタマイズ"], "prototype_slots": 2},
    "JGU": {"name": "神宮本社", "hp": -1, "mp": 2, "mv": -1, "skill_max": "R+1",
            "restrict": ["流派奥義不可", "術決別不可"]},
    "IYO": {"name": "陰陽寮", "parent": "JGU",
            "auto": ["古流式法"], "restrict": ["発動DIF+1", "1T1術"]},
    "YGU": {"name": "八雲大社", "parent": "JGU"},
    "DKS": {"name": "神宮大工衆", "parent": "JGU"},
    "KSS": {"name": "呪殺師", "parent": "CUR"},
    "JYS": {"name": "呪与師", "parent": "CUR"},
    "SSS": {"name": "屍術師", "parent": "CUR"},
    "WIT": {"name": "魔女", "hp": -1, "mp": 2, "ed": -1, "skill_max": "R+1",
            "auto": ["基本のガンド"]},
    "SHA": {"name": "原始儀術者", "hp": -1, "mp": 2, "ed": -1, "skill_max": "R+1",
            "auto": ["憑霊", "霊格修復"]},
    "LIB": {"name": "蔵書家", "hp": -1, "mp": 2, "ed": -1, "skill_max": "R+1",
            "auto": ["頁の作成", "頁の実行"]},
    "KIS": {"name": "騎士修道会", "restrict": ["流派奥義不可"]},
    "INQ": {"name": "儀術査問部", "parent": "KIS", "auto": ["仮初の釘", "隠密歩法"]},
    "SAC": {"name": "秘徴典拝部", "parent": "KIS", "auto": ["秘跡詠唱", "秘跡照準"]},
    "IMP": {"name": "異植者", "mp": -1, "hp": 1, "skill_max": "B+1"},
    "BAS": {"name": "渾血者", "mp": -1, "hp": 1, "mv": 1, "skill_max": "B+1"},
    "MAR": {"name": "来訪者", "mp": 2, "hp": -1, "skill_max": "最高+2"},
}


# ── 武器 [D5][K] ──────────────────────────────────────────────────────────────

WEAPONS: dict[str, dict[str, Any]] = {
    "small_mel":  {"name": "小型近接", "hands": 1, "cost": 5, "chk": "K",   "dmg": "2pd",
                   "styles": ["連撃", "精密攻撃"]},
    "medium_mel": {"name": "中型近接", "hands": 1, "cost": 5, "chk": "B|K", "dmg": "3pd",
                   "styles": ["精密攻撃", "強攻撃"]},
    "large_mel":  {"name": "大型近接", "hands": 2, "cost": 5, "chk": "B",   "dmg": "3pd",
                   "styles": ["強攻撃", "全力攻撃"]},
    "spirit_mel": {"name": "霊的近接", "hands": 2, "cost": 5, "chk": "R",   "dmg": "1d6rd"},
    "small_rng":  {"name": "小型遠隔", "hands": 1, "cost": 5, "chk": "K",   "dmg": "2pd",
                   "styles": ["2回射撃"]},
    "medium_rng": {"name": "中型遠隔", "hands": 2, "cost": 5, "chk": "K",   "dmg": "4pd",
                   "styles": ["連射"]},
    "large_rng":  {"name": "大型遠隔", "hands": 2, "cost": 5, "chk": "K",   "dmg": "5pd",
                   "styles": ["狙撃"]},
    "spirit_rng": {"name": "霊的遠隔", "hands": 2, "cost": 5, "chk": "R",   "dmg": "1d3rd"},
}


# ── 防具 [D6] ─────────────────────────────────────────────────────────────────

ARMORS: dict[str, dict[str, Any]] = {
    "light":  {"name": "軽装", "cost": 5, "ed": 2, "arm": 0, "tm_penalty": 0},
    "medium": {"name": "中装", "cost": 5, "ed": 1, "arm": 1, "tm_penalty": 0},
    "heavy":  {"name": "重装", "cost": 5, "ed": 0, "arm": 3, "tm_penalty": 1},
}


# ── スキル [D7][F] ────────────────────────────────────────────────────────────

SKILLS: dict[str, dict[str, Any]] = {
    "martial":      {"name": "白兵戦適性",   "cost": 5, "effect": {"mel_dice": 2}},
    "ranged":       {"name": "射撃戦適性",   "cost": 5, "effect": {"rng_dice": 2}},
    "general":      {"name": "汎用祭具適性", "cost": 5, "effect": {"mel_dice": 1, "rng_dice": 1}},
    "endurance":    {"name": "強靭身体",     "cost": 5, "effect": {"b_chk": 2, "hp": 2}},
    "curse_resist": {"name": "被呪耐性",     "cost": 5, "effect": {"r_chk": 2, "mp": 2}},
    "mobility":     {"name": "機動戦適性",   "cost": 5, "effect": {"mv": 2, "tm_dif": -1}},
    "evasion":      {"name": "回避体術",     "cost": 5, "effect": {"ed": 2}},
}


# ── 祓魔術 [D4][I] ────────────────────────────────────────────────────────────

ARTS: dict[str, dict[str, Any]] = {
    # 基本
    "guard_wall":    {"name": "加護防壁",   "cost": 1, "tier": "base"},
    "anti_step":     {"name": "反閇歩法",   "cost": 1, "tier": "base",
                      "effect": {"b": 3, "mv": 2}},
    "spirit_release":{"name": "霊力放出",   "cost": 1, "tier": "base", "dmg": "2rd", "area": "3x3"},
    "spirit_bullet": {"name": "霊弾発射",   "cost": 1, "tier": "base"},
    "curse_word":    {"name": "呪祝詛詞",   "cost": 1, "tier": "base"},
    "shikigami":     {"name": "式神使役",   "cost": 2, "tier": "base"},
    # Ex-5
    "kago_zokei":    {"name": "加護造形",   "cost": 2, "tier": "ex5"},
    "isou_tenkai":   {"name": "異装纏怪",   "cost": 1, "tier": "ex5"},
    "jutsumyaku":    {"name": "術絡祈祷",   "cost": 1, "tier": "ex5"},
    "nokonoko":      {"name": "後々先々",   "cost": 1, "tier": "ex5"},
    "shikiso_copy":  {"name": "式素複製",   "cost": 1, "tier": "ex5"},
    "roku_san":      {"name": "六三目連",   "cost": "var", "tier": "ex5"},
    # Ex-10
    "kago_bunshin":  {"name": "加護分身",   "cost": 2, "tier": "ex10"},
    "fukatsu_toyo":  {"name": "賦活投与",   "cost": 1, "tier": "ex10"},
    "kessen_kaiho":  {"name": "血旋廻法",   "cost": 1, "tier": "ex10"},
    "narukami":      {"name": "鳴神体質",   "cost": 1, "tier": "ex10"},
    "anei_kaiga":    {"name": "闇影怪画",   "cost": 1, "tier": "ex10"},
    "jukai_kengen":  {"name": "受怪顕現",   "cost": 1, "tier": "ex10"},
}


# ── 奥義 [H Ex-2] ─────────────────────────────────────────────────────────────

OUGI: dict[str, dict[str, Any]] = {
    "fukitobashi":   {"name": "吹き飛ばし", "stat": "B",   "type": "universal"},
    "yoroi_kudaki":  {"name": "鎧砕き",     "stat": "B",   "type": "universal"},
    "reiki":         {"name": "霊輝",       "stat": "R",   "type": "universal"},
    "mitama_shibari":{"name": "御霊縛り",   "stat": "R",   "type": "universal"},
    "tsubame_gaeshi":{"name": "燕返し",     "stat": "K",   "type": "universal"},
    "sasanuki":      {"name": "笹貫",       "stat": "K",   "type": "universal"},
    "botan":         {"name": "牡丹",       "stat": "B",   "school": "祓魔一刀流"},
    "kakitsubata":   {"name": "燕子花",     "stat": "K",   "school": "祓魔一刀流"},
    "higanzakura":   {"name": "彼岸桜",     "stat": "B|K", "school": "祓魔一刀流"},
}


# ── 算出ユーティリティ ────────────────────────────────────────────────────────

@dataclass
class DerivedStats:
    """B/R/K/A と装備・スキルから算出される副次ステータス。"""

    hp: int
    mp: int
    mv: int
    ed: int
    arm: int
    notes: list[str] = field(default_factory=list)


def _sum_effect(items: list[dict[str, Any]], key: str) -> int:
    total = 0
    for it in items:
        eff = it.get("effect") or {}
        v = eff.get(key)
        if isinstance(v, int):
            total += v
    return total


def derive_stats(
    *,
    body: int,
    soul: int,
    skill: int,
    magic: int,
    org: str | None = None,
    armor: str | None = None,
    skill_keys: list[str] | None = None,
) -> DerivedStats:
    """B/R/K/A・組織・装備・選択スキルから副次ステータスを算出する。

    - HP = B + 組織hp補正 + skillEffect(hp)
    - MP = R + 組織mp補正 + skillEffect(mp)
    - MV = ceil(max(B, K) / 2) + 組織mv補正 + skillEffect(mv) (重装時は最低2)
    - ED = max(B, R, K) + armor.ed + 組織ed補正 + skillEffect(ed)
    - ARM = armor.arm
    """
    skill_keys = skill_keys or []
    chosen_skills = [SKILLS[k] for k in skill_keys if k in SKILLS]

    org_data = ORGS.get(org or "", {})
    armor_data = ARMORS.get(armor or "", {})

    hp = body + int(org_data.get("hp", 0)) + _sum_effect(chosen_skills, "hp")
    mp = soul + int(org_data.get("mp", 0)) + _sum_effect(chosen_skills, "mp")

    mv_base = ceil(max(body, skill) / 2)
    mv = mv_base + int(org_data.get("mv", 0)) + _sum_effect(chosen_skills, "mv")
    if armor_data.get("tm_penalty"):
        mv = max(2, mv - int(armor_data.get("tm_penalty", 0)))
    mv = max(2, mv)

    ed = (
        max(body, soul, skill)
        + int(armor_data.get("ed", 0))
        + int(org_data.get("ed", 0))
        + _sum_effect(chosen_skills, "ed")
    )
    arm = int(armor_data.get("arm", 0))

    notes: list[str] = []
    if org_data.get("restrict"):
        notes.append(f"組織制限: {', '.join(org_data['restrict'])}")
    if org_data.get("auto"):
        notes.append(f"自動取得: {', '.join(org_data['auto'])}")

    return DerivedStats(hp=hp, mp=mp, mv=mv, ed=ed, arm=arm, notes=notes)


# ── 選択肢ヘルパー (GUI 向け) ─────────────────────────────────────────────────

def org_choices() -> list[tuple[str, str]]:
    return [(k, f"{k} / {v['name']}") for k, v in ORGS.items()]


def weapon_choices() -> list[tuple[str, str]]:
    return [(k, f"{v['name']} (chk:{v['chk']} dmg:{v['dmg']})") for k, v in WEAPONS.items()]


def armor_choices() -> list[tuple[str, str]]:
    return [
        (k, f"{v['name']} (ED+{v['ed']}/ARM+{v['arm']})")
        for k, v in ARMORS.items()
    ]


def skill_choices() -> list[tuple[str, str]]:
    return [(k, v["name"]) for k, v in SKILLS.items()]


def art_choices() -> list[tuple[str, str]]:
    return [(k, f"{v['name']}[{v['tier']}] cost:{v['cost']}") for k, v in ARTS.items()]
