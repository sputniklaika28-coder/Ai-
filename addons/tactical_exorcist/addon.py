"""タクティカル祓魔師 TRPG ルールシステムアドオン。

ゲーム固有のフェイズキーワード・プロンプトテンプレート・世界観設定を
Core Engine に提供する。

付属機能:
  - char_maker.py  : タクティカル祓魔師専用キャラクターメーカー (GUI)
  - system_generator.py : NPC/敵/アイテム/シナリオフック生成ツール
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.addons.addon_base import AddonContext, RuleSystemAddon, ToolExecutionContext

logger = logging.getLogger(__name__)


class TacticalExorcistAddon(RuleSystemAddon):
    """タクティカル祓魔師 TRPG ルールシステム。

    Provides:
      - フェイズ検出キーワード
      - GMシステムプロンプト
      - 世界観テキスト
      - キャラクターシート解釈
      - NPC/敵/アイテム/シナリオフック生成ツール (system_generator 経由)
    """

    # ──────────────────────────────────────────
    # フェイズ定義（旧 SessionContext._PHASE_KEYWORDS / _PHASE_ORDER から抽出）
    # ──────────────────────────────────────────

    PHASE_KEYWORDS: dict[str, list[str]] = {
        "combat": ["戦闘開始", "戦闘スタート", "エンカウント", "敵が現れ"],
        "mission": ["ミッション開始", "ミッションフェイズ", "突入"],
        "assessment": ["査定フェイズ", "帰還"],
        "briefing": ["ブリーフィング"],
    }

    PHASE_ORDER: dict[str, int] = {
        "free": 0,
        "briefing": 1,
        "mission": 2,
        "combat": 3,
        "assessment": 4,
    }

    def on_load(self, context: AddonContext) -> None:
        self._root = self.addon_dir
        self._configs_dir: Path = context.root_dir / "configs"
        self._prompts: dict | None = None
        self._world_setting: str = ""
        self._world_setting_json: dict | None = None
        self._reference_character: str = ""
        self._generator = None
        self._lm_client = context.lm_client

        # プロンプトテンプレート読み込み
        prompts_path = self._root / (self.manifest.prompts_override or "prompts.json")
        if prompts_path.exists():
            try:
                with open(prompts_path, encoding="utf-8") as f:
                    self._prompts = json.load(f)
                logger.info("プロンプトテンプレート読み込み: %s", prompts_path)
            except Exception as e:
                logger.warning("プロンプト読み込み失敗: %s", e)

        # 世界観設定 (圧縮テキスト) 読み込み: configs/ 配下を参照する
        ws_filename = self.manifest.world_setting or "world_setting_compressed.txt"
        ws_path = self._configs_dir / ws_filename
        if ws_path.exists():
            try:
                self._world_setting = ws_path.read_text(encoding="utf-8")
                logger.info("世界観設定読み込み: %s (%d文字)", ws_path, len(self._world_setting))
            except Exception as e:
                logger.warning("世界観設定読み込み失敗: %s", e)
        else:
            logger.warning("世界観設定ファイルが見つかりません: %s", ws_path)

        # 参考キャラクター (few-shot 用) 読み込み
        ref_path = self._configs_dir / "reference_character.json"
        if ref_path.exists():
            try:
                self._reference_character = ref_path.read_text(encoding="utf-8").strip()
                logger.info(
                    "参考キャラクター読み込み: %s (%d文字)",
                    ref_path,
                    len(self._reference_character),
                )
            except Exception as e:
                logger.warning("参考キャラクター読み込み失敗: %s", e)

        # 世界観設定 (構造化 JSON) 読み込み: configs/ 配下を参照する
        ws_json_filename = self.manifest.world_setting_json or "world_setting.json"
        ws_json_path = self._configs_dir / ws_json_filename
        if ws_json_path.exists():
            try:
                with open(ws_json_path, encoding="utf-8") as f:
                    self._world_setting_json = json.load(f)
                logger.info("世界観JSON読み込み: %s", ws_json_path)
            except Exception as e:
                logger.warning("世界観JSON読み込み失敗: %s", e)

        # 専用ジェネレーター読み込み
        try:
            from importlib.util import module_from_spec, spec_from_file_location

            gen_path = self._root / "system_generator.py"
            if gen_path.exists():
                spec = spec_from_file_location("tactical_exorcist.system_generator", gen_path)
                if spec and spec.loader:
                    mod = module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    self._generator = mod.TacticalExorcistGenerator()
                    logger.info("システムジェネレーター読み込み完了")
        except Exception as e:
            logger.warning("システムジェネレーター読み込み失敗: %s", e)

    # ──────────────────────────────────────────
    # RuleSystemAddon インターフェース
    # ──────────────────────────────────────────

    def get_system_prompt_override(self) -> str | None:
        """GMプロンプトのシステムテキストを返す。"""
        if self._prompts is None:
            return None
        templates = self._prompts.get("templates", {})
        meta_gm = templates.get("meta_gm_template", {})
        return meta_gm.get("system")

    def get_world_setting(self) -> str:
        return self._world_setting

    def get_world_setting_json(self) -> dict | None:
        return self._world_setting_json

    def get_phase_keywords(self) -> dict[str, list[str]]:
        return self.PHASE_KEYWORDS

    def get_prompt_templates(self) -> dict | None:
        return self._prompts

    # ──────────────────────────────────────────
    # キャラクター汎用フック（CharacterService が使用）
    # ──────────────────────────────────────────

    def get_character_sheet_template(self) -> dict:
        """タクティカル祓魔師の空シートテンプレート。"""
        return {
            "name": "",
            "alias": "",
            "hp": 15, "sp": 15, "evasion": 2, "mobility": 3, "armor": 0,
            "body": 3, "soul": 3, "skill": 3, "magic": 3,
            "items": {
                "katashiro": 1, "haraegushi": 0, "shimenawa": 0,
                "juryudan": 0, "ireikigu": 0, "meifuku": 0, "jutsuyen": 0,
            },
            "memo": "",
            "skills": [],
            "weapons": [],
        }

    def get_character_generation_schema(self) -> type:
        """generate_structured で直接シートを生成するためのスキーマ。"""
        from core.schemas import TacticalExorcistSheet

        return TacticalExorcistSheet

    def build_vtt_piece_data(self, sheet: dict) -> dict:
        """シート dict から CCFolia 貼り付け用ペイロードを構築する。

        AI 生成時に CCFolia 形式のペイロードが `_vtt_piece_raw` に保存されていれば
        それをそのまま返す（AI が組んだ memo / commands / status / params を尊重）。
        それ以外は flat フィールドから status(10項目)・params(6項目)・commands を
        組み上げる従来ロジックへフォールバックする。
        """
        return build_ccfolia_piece_from_sheet(sheet)

    def build_character_generation_prompt(
        self, concept: str
    ) -> tuple[str, str] | None:
        """world_setting + 参考シートを丸ごと注入したキャラ生成プロンプトを返す。

        qwen3 で実証済みの「ルール本文 + 参考1枚 → 完成 CCFolia JSON」方式。
        """
        if not self._world_setting:
            return None

        system_prompt = (
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
            "\n"
            "========== ルールブック本文 ==========\n"
            f"{self._world_setting}\n"
            "========== ルールブック本文 ここまで ==========\n"
        )
        if self._reference_character:
            system_prompt += (
                "\n========== 参考キャラクター（出力フォーマット例） ==========\n"
                f"{self._reference_character}\n"
                "========== 参考キャラクター ここまで ==========\n"
                "上記と同じ JSON 構造・memo/commands の粒度で出力すること。\n"
            )

        user_message = (
            f"ユーザー要望: {concept}\n\n"
            "この要望を満たすタクティカル祓魔師 PC を 1 人作成し、"
            "CCFolia 貼付用 JSON だけを出力してください。"
        )
        return system_prompt, user_message

    def interpret_character_sheet(self, sheet_data: dict) -> str:
        """キャラクターシートを自然言語テキストに変換する。"""
        name = sheet_data.get("name", "不明")
        alias = sheet_data.get("alias", "")
        hp = sheet_data.get("hp", "?")
        sp = sheet_data.get("sp", "?")
        body = sheet_data.get("body", "?")
        soul = sheet_data.get("soul", "?")
        skill = sheet_data.get("skill", "?")
        magic = sheet_data.get("magic", "?")
        evasion = sheet_data.get("evasion", "?")
        mobility = sheet_data.get("mobility", "?")
        armor = sheet_data.get("armor", "?")
        memo = sheet_data.get("memo", "")

        items = sheet_data.get("items", {})
        item_parts = []
        item_labels = {
            "katashiro": "形代",
            "haraegushi": "祓串",
            "shimenawa": "注連鋼縄",
            "juryudan": "呪瘤檀",
            "ireikigu": "医霊器具",
            "meifuku": "名伏",
            "jutsuyen": "術延起点",
        }
        for key, label in item_labels.items():
            val = items.get(key, 0)
            if val > 0:
                item_parts.append(f"{label}×{val}")

        skills = sheet_data.get("skills", [])
        weapons = sheet_data.get("weapons", [])

        lines = [f"【{name}】" + (f"（{alias}）" if alias else "")]
        lines.append(f"体力:{hp} 霊力:{sp} 回避D:{evasion} 機動力:{mobility} 装甲:{armor}")
        lines.append(f"体:{body} 霊:{soul} 巧:{skill} 術:{magic}")
        if item_parts:
            lines.append("装備: " + "、".join(item_parts))
        if skills:
            lines.append("特技: " + "、".join(s.get("name", "") for s in skills))
        if weapons:
            lines.append("武器: " + "、".join(w.get("name", "") for w in weapons))
        if memo:
            lines.append(f"設定: {memo[:80]}{'…' if len(memo) > 80 else ''}")
        return "\n".join(lines)

    # ──────────────────────────────────────────
    # ツール定義（system_generator 付属ツール）
    # ──────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        """システムジェネレーターが提供するツールを返す。"""
        if self._generator is None:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "generate_npc",
                    "description": "タクティカル祓魔師世界観に合ったNPCを生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "NPCのコンセプト（例: 厳格な上官、謎めいた情報屋）",
                            },
                            "role": {
                                "type": "string",
                                "enum": ["ally", "neutral", "enemy"],
                                "description": "NPCの役割",
                            },
                        },
                        "required": ["concept"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_enemy",
                    "description": "タクティカル祓魔師用の敵（霊体・怪異）を生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "敵のコンセプト（例: 高速移動型の霊体、集団で行動する低級怪異）",
                            },
                            "threat_level": {
                                "type": "string",
                                "enum": ["weak", "normal", "strong", "boss"],
                                "description": "脅威レベル",
                            },
                        },
                        "required": ["concept"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_item",
                    "description": "タクティカル祓魔師世界観のアイテム・祭具を生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept": {
                                "type": "string",
                                "description": "アイテムのコンセプト（例: 遠距離用の祓具、防御特化の結界アイテム）",
                            },
                        },
                        "required": ["concept"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_scenario_hook",
                    "description": "タクティカル祓魔師のシナリオフック（導入きっかけ）を生成する",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "setting": {
                                "type": "string",
                                "description": "場所や状況の概要（例: 廃校、繁華街の夜）",
                            },
                            "num_hooks": {
                                "type": "integer",
                                "description": "生成するフック数（1〜5）",
                                "minimum": 1,
                                "maximum": 5,
                            },
                        },
                        "required": ["setting"],
                    },
                },
            },
        ]

    def execute_tool(
        self, tool_name: str, tool_args: dict, context: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """ジェネレーターツールを実行する。"""
        if self._generator is None:
            return False, json.dumps({"error": "ジェネレーターが利用できません"}, ensure_ascii=False)

        try:
            if tool_name == "generate_npc":
                result = self._generator.generate_npc(
                    self._lm_client,
                    concept=tool_args.get("concept", ""),
                    role=tool_args.get("role", "neutral"),
                )
            elif tool_name == "generate_enemy":
                result = self._generator.generate_enemy(
                    self._lm_client,
                    concept=tool_args.get("concept", ""),
                    threat_level=tool_args.get("threat_level", "normal"),
                )
            elif tool_name == "generate_item":
                result = self._generator.generate_item(
                    self._lm_client,
                    concept=tool_args.get("concept", ""),
                )
            elif tool_name == "generate_scenario_hook":
                result = self._generator.generate_scenario_hook(
                    self._lm_client,
                    setting=tool_args.get("setting", ""),
                    num_hooks=tool_args.get("num_hooks", 3),
                )
            else:
                return False, json.dumps({"error": f"未知のツール: {tool_name}"}, ensure_ascii=False)

            return False, json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error("ツール実行エラー %s: %s", tool_name, e)
            return False, json.dumps({"error": str(e)}, ensure_ascii=False)

    # ──────────────────────────────────────────
    # キャラメーカー起動ヘルパー
    # ──────────────────────────────────────────

    def launch_char_maker(self) -> None:
        """タクティカル祓魔師専用キャラクターメーカーをGUIで起動する。"""
        import subprocess
        import sys

        char_maker_path = self.addon_dir / "char_maker.py"
        if not char_maker_path.exists():
            logger.error("char_maker.py が見つかりません: %s", char_maker_path)
            return
        subprocess.Popen([sys.executable, str(char_maker_path)])
        logger.info("キャラクターメーカーを起動しました")


# ──────────────────────────────────────────
# CCFolia ペイロードビルダー
# ──────────────────────────────────────────


def build_ccfolia_piece_from_sheet(sheet: dict) -> dict:
    """シート dict から CCFolia 貼り付け用 `{"kind":"character","data":{...}}` を返す。

    `_vtt_piece_raw` が残っていれば AI が組んだ memo/commands/status/params を
    そのまま返す（名前だけは UI 側の変更を優先）。そうでなければ flat フィールド
    から status(10項目)・params(6項目)・commands(固定テンプレ) を組み上げる。
    """
    raw = sheet.get("_vtt_piece_raw")
    if (
        isinstance(raw, dict)
        and raw.get("kind") == "character"
        and isinstance(raw.get("data"), dict)
    ):
        piece = {"kind": "character", "data": dict(raw["data"])}
        ui_name = sheet.get("name")
        if ui_name:
            piece["data"]["name"] = ui_name
        return piece

    def _i(key: str, default: int = 0) -> int:
        v = sheet.get(key, default)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    items = sheet.get("items") or {}

    def _item(key: str) -> int:
        v = items.get(key, 0)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    name = sheet.get("name") or "名無し"
    alias = sheet.get("alias", "")
    memo_body = sheet.get("memo", "") or ""
    memo = f"【二つ名】{alias}\n\n{memo_body}" if alias else memo_body

    hp = _i("hp", 15)
    sp = _i("sp", 15)
    evasion = _i("evasion", 2)
    status = [
        {"label": "体力", "value": hp, "max": hp},
        {"label": "霊力", "value": sp, "max": sp},
        {"label": "回避D", "value": evasion, "max": evasion},
        {"label": "形代", "value": _item("katashiro"), "max": _item("katashiro")},
        {"label": "祓串", "value": _item("haraegushi"), "max": _item("haraegushi")},
        {"label": "注連鋼縄", "value": _item("shimenawa"), "max": _item("shimenawa")},
        {"label": "呪瘤檀", "value": _item("juryudan"), "max": _item("juryudan")},
        {"label": "医霊器具", "value": _item("ireikigu"), "max": _item("ireikigu")},
        {"label": "名伏", "value": _item("meifuku"), "max": _item("meifuku")},
        {"label": "術延起点", "value": _item("jutsuyen"), "max": _item("jutsuyen")},
    ]
    params = [
        {"label": "体", "value": str(_i("body", 3))},
        {"label": "霊", "value": str(_i("soul", 3))},
        {"label": "巧", "value": str(_i("skill", 3))},
        {"label": "術", "value": str(_i("magic", 3))},
        {"label": "機動力", "value": str(_i("mobility", 3))},
        {"label": "装甲", "value": str(_i("armor", 0))},
    ]
    return {
        "kind": "character",
        "data": {
            "name": name,
            "initiative": 0,
            "memo": memo,
            "commands": _build_ccfolia_commands(sheet),
            "status": status,
            "params": params,
        },
    }


def _build_ccfolia_commands(sheet: dict) -> str:
    """シートから CCFolia 用チャットパレットコマンド文字列を生成する。"""
    lines: list[str] = []

    lines.append("◆能力値を使った判定◆")
    lines.append("{体}b6=>4  //【体】判定")
    lines.append("{霊}b6=>4  //【霊】判定")
    lines.append("{巧}b6=>4  //【巧】判定")
    lines.append("{術}b6=>4  //【術】判定")
    lines.append("")

    lines.append("◆戦闘中用の判定◆")
    lines.append("{巧}b6=>4  //戦術機動")
    lines.append("({体})b6=>4  //近接攻撃")
    lines.append("({巧})b6=>4  //遠隔攻撃")
    lines.append("({霊})b6=>4  //霊的攻撃")
    lines.append("({術})b6=>4  //術発動")
    lines.append("")

    lines.append("2d6  //ダメージ")
    lines.append("1d3  //霊的ダメージ")
    lines.append("b6=>4  //回避判定")
    lines.append("")

    lines.append("C({体力})  //残り体力")
    lines.append("C({霊力})  //残り霊力")
    lines.append("")

    lines.append("◆支給装備◆")
    lines.append(
        "【形代】：キャラクターが「死亡」した時、①【形代】を1つ消費することで「死亡」を回避する"
        "②【体力】【霊力】を半分まで回復した状態でマップ上の「リスポーン地点」にキャラクターを戻す。"
        "　また、手番中に好きなタイミングで【形代】を1つ消費することで、キャラクターは【霊力】を2点回復することができる。"
    )
    lines.append("")
    lines.append(
        "【祓串】：1つ消費することで自身を中心とした7*7マスのどこかに配置するか、"
        "近接攻撃または遠隔攻撃に使用できる。近接攻撃に使用した場合は1d6点、"
        "遠隔攻撃に使用した場合は3点の「物理ダメージ」を与える。"
    )
    lines.append("")
    lines.append(
        "【注連鋼縄】：3つ消費することで、【巧】の値を参照してマップ上に設置する。"
        "結界に関するルールは2-7：結界の設置についてを参照。"
    )
    lines.append("")
    lines.append(
        "【呪瘤檀】：攻撃の代わりにこのアイテムを使用する。"
        "自分を中心とした5＊5マスのいずれかのマス1つを「中心」に定め、"
        "「中心」と隣接する3＊3のマスにいるキャラクター全員に2点の霊的ダメージを与える（回避は『難易度：NORMAL』）。"
    )
    lines.append("")

    skills = sheet.get("skills") or []
    if skills:
        lines.append("◆特技◆")
        for s in skills:
            if isinstance(s, dict):
                lines.append(f"【{s.get('name', '')}】：{s.get('description', '')}")
                lines.append("")

    weapons = sheet.get("weapons") or []
    if weapons:
        lines.append("◆攻撃祭具◆")
        for w in weapons:
            if isinstance(w, dict):
                lines.append(f"【{w.get('name', '')}】：{w.get('description', '')}")
                lines.append("")

    lines.append("[Credit: 非公式タクティカル祓魔師キャラクターシートVer0.8 著作者様]")
    return "\n".join(lines)
