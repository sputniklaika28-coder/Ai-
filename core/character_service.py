"""character_service.py — キャラクターの保管・生成・VTT コマ化を統一するサービス層。

これまで char_maker.py (core 版 / tactical_exorcist 版) / launcher_v2::ActorsView に
それぞれ別々に実装されていた以下 3 つの操作を、ルールシステムアドオンを介した
汎用的な API として提供する:

  - generate_from_concept : コンセプト文字列から AI でシートを生成
  - save_bundle           : configs/saved_pcs/ と configs/characters.json を同時更新
  - to_vtt_clipboard      : CCFolia 貼付用 JSON 文字列を返す

各操作はアクティブな RuleSystemAddon に委譲されるため、tactical_exorcist 以外の
システムでもそのまま動く（デフォルト実装にフォールバック）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.addons.addon_base import RuleSystemAddon
    from core.addons.addon_manager import AddonManager
    from core.character_manager import CharacterManager
    from core.lm_client import LMClient

logger = logging.getLogger(__name__)


# ──────────────────────────────────────
# CharacterBundle: 単一の真実としての "キャラクターの束"
# ──────────────────────────────────────


@dataclass
class CharacterBundle:
    """1 キャラクターを表す 3 つの側面をまとめたデータ。"""

    sheet: dict = field(default_factory=dict)
    """configs/saved_pcs/<name>.json に書き出されるシート本体。"""

    roster_entry: dict = field(default_factory=dict)
    """configs/characters.json に書き出される identity レコード。"""

    persona: dict = field(default_factory=dict)
    """口調・NG行動などの persona ヒント。sheet._persona に格納される。"""

    system_prompt: str = ""
    """PersonaBuilder が生成した LLM 用システムプロンプト。任意。"""

    @property
    def name(self) -> str:
        return (
            self.sheet.get("name")
            or self.roster_entry.get("name")
            or "名無し"
        )


# ──────────────────────────────────────
# CharacterService
# ──────────────────────────────────────


class CharacterService:
    """キャラクター関連操作の統一ファサード。"""

    def __init__(
        self,
        *,
        saved_pcs_dir: Path,
        characters_file: Path,
        character_manager: "CharacterManager | None" = None,
        addon_manager: "AddonManager | None" = None,
        lm_client: "LMClient | None" = None,
    ) -> None:
        self._saved_pcs_dir = Path(saved_pcs_dir)
        self._saved_pcs_dir.mkdir(parents=True, exist_ok=True)
        self._characters_file = Path(characters_file)
        self._addon_manager = addon_manager
        self._lm_client = lm_client

        # character_manager 未指定なら内部で生成
        if character_manager is None:
            from core.character_manager import CharacterManager

            character_manager = CharacterManager(config_path=str(self._characters_file))
        self._character_manager = character_manager

    # ──────────────────────────────────────
    # ルールシステム取得
    # ──────────────────────────────────────

    def _active_rule_system(self) -> "RuleSystemAddon | None":
        if self._addon_manager is None:
            return None
        try:
            return self._addon_manager.get_active_rule_system()
        except Exception:  # AddonManager が未初期化な場合
            return None

    # ──────────────────────────────────────
    # 保管: sheet と roster を同時更新
    # ──────────────────────────────────────

    def save_bundle(self, bundle: CharacterBundle, *, is_ai: bool | None = None) -> Path:
        """束を `saved_pcs/<name>.json` に保存し、`characters.json` にも upsert する。

        Returns:
            書き込まれたシートファイルの絶対パス。
        """
        sheet = dict(bundle.sheet)
        name = sheet.get("name") or bundle.name
        if not name or not name.strip():
            raise ValueError("save_bundle: name が空です")
        sheet["name"] = name

        if bundle.persona and "_persona" not in sheet:
            sheet["_persona"] = bundle.persona

        sheet_path = self._saved_pcs_dir / f"{_safe_filename(name)}.json"
        sheet_path.write_text(
            json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("シート保存: %s", sheet_path)

        # roster 更新
        from core.character_manager import _slugify

        entry = dict(bundle.roster_entry)
        entry.setdefault("name", name)
        entry.setdefault("layer", "player")
        entry.setdefault("role", "player")
        entry.setdefault("enabled", True)
        if is_ai is not None:
            entry["is_ai"] = is_ai
        else:
            entry.setdefault("is_ai", False)
        entry["id"] = entry.get("id") or _slugify(name)
        entry["sheet_file"] = str(
            sheet_path.relative_to(self._characters_file.parent.parent)
            if self._characters_file.parent.parent in sheet_path.parents
            else sheet_path
        )
        self._character_manager.upsert_character(entry)
        return sheet_path

    # ──────────────────────────────────────
    # 読込: 名前から bundle を復元
    # ──────────────────────────────────────

    def load_bundle(self, name: str) -> CharacterBundle | None:
        """保存済みシートから bundle を復元する。対応する roster_entry があれば結合。"""
        sheet_path = self._saved_pcs_dir / f"{_safe_filename(name)}.json"
        if not sheet_path.exists():
            matches = list(self._saved_pcs_dir.glob(f"*{name}*.json"))
            if not matches:
                return None
            sheet_path = matches[0]

        try:
            sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("シート読込失敗 %s: %s", sheet_path, e)
            return None

        # roster 側にマッチするエントリがあれば取得
        roster = {}
        for cid, ent in self._character_manager.characters.items():
            if ent.get("name") == sheet.get("name"):
                roster = dict(ent)
                break

        persona = sheet.get("_persona", {})
        return CharacterBundle(sheet=sheet, roster_entry=roster, persona=persona)

    def list_saved(self) -> list[str]:
        return [f.stem for f in sorted(self._saved_pcs_dir.glob("*.json"))]

    def delete(self, name: str) -> bool:
        sheet_path = self._saved_pcs_dir / f"{_safe_filename(name)}.json"
        if not sheet_path.exists():
            return False
        sheet_path.unlink()
        return True

    # ──────────────────────────────────────
    # VTT コマとしてコピー
    # ──────────────────────────────────────

    def build_vtt_piece(self, sheet: dict) -> dict:
        """アクティブなルールシステムを使って VTT ペイロードを組み立てる。"""
        rule = self._active_rule_system()
        if rule is not None:
            try:
                return rule.build_vtt_piece_data(sheet)
            except Exception as e:
                logger.warning("build_vtt_piece_data エラー、デフォルトにフォールバック: %s", e)

        # フォールバック: 最小限
        name = sheet.get("name") or "名無し"
        memo = sheet.get("memo", "")
        return {
            "kind": "character",
            "data": {
                "name": name,
                "initiative": 0,
                "memo": memo,
                "commands": "",
                "status": [],
                "params": [],
            },
        }

    def to_vtt_clipboard(self, sheet: dict) -> str:
        """CCFolia 貼付用 JSON 文字列を返す。"""
        return json.dumps(self.build_vtt_piece(sheet), ensure_ascii=False)

    # ──────────────────────────────────────
    # AI 生成
    # ──────────────────────────────────────

    def generate_from_concept(
        self,
        concept_text: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> CharacterBundle | None:
        """コンセプト文字列から bundle を AI 生成する。

        優先順位:
          1. ルールシステムが `build_character_generation_prompt` を実装していれば
             それを使って world_setting + 参考シートを丸ごと注入した素のテキスト
             生成を行い、CCFolia 形式の JSON を直接受け取る（qwen3 実証済みパス）。
          2. それ以外で Pydantic スキーマがあれば structured 生成にフォールバック。
          3. 汎用パスとして PersonaBuilder に委譲。
        """
        if self._lm_client is None:
            logger.warning("generate_from_concept: LMClient が未設定")
            return None

        rule = self._active_rule_system()

        # --- (1) world_setting 注入型のプロンプトパス ---
        if rule is not None:
            try:
                prompt_pair = rule.build_character_generation_prompt(concept_text)
            except Exception as e:
                logger.warning("build_character_generation_prompt エラー: %s", e)
                prompt_pair = None
            if prompt_pair is not None:
                sheet = self._generate_sheet_via_prompt(
                    prompt_pair, temperature=temperature, max_tokens=max_tokens
                )
                if sheet is not None:
                    return CharacterBundle(
                        sheet=sheet,
                        roster_entry={
                            "name": sheet.get("name", ""),
                            "description": (sheet.get("memo", "") or "")[:200],
                        },
                        persona=sheet.get("_persona", {}),
                    )

        # --- (2) 構造化スキーマパス (後方互換) ---
        schema = None
        if rule is not None:
            try:
                schema = rule.get_character_generation_schema()
            except Exception as e:
                logger.warning("get_character_generation_schema エラー: %s", e)

        from core.schemas import CharacterConceptOutput

        if schema is not None and schema is not CharacterConceptOutput:
            sheet = self._generate_sheet_direct(rule, concept_text, schema, temperature, max_tokens)
            if sheet is None:
                return None
            return CharacterBundle(
                sheet=sheet,
                roster_entry={"name": sheet.get("name", ""), "description": sheet.get("memo", "")[:200]},
                persona=sheet.get("_persona", {}),
            )

        # --- (3) 汎用パス ---
        return self._generate_via_persona_builder(concept_text)

    def _generate_sheet_via_prompt(
        self,
        prompt_pair: tuple[str, str],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict | None:
        """`(system_prompt, user_message)` からそのまま CCFolia JSON を得るパス。

        ルールシステム側で world_setting と参考シートを詰め込んでいる前提。
        受け取った JSON はフラットシートに展開し、原形を `_vtt_piece_raw` へ保存する。
        """
        assert self._lm_client is not None
        system_prompt, user_message = prompt_pair
        try:
            content, _tool_calls = self._lm_client.generate_response_sync(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=None,
            )
        except Exception as e:
            logger.error("generate_response_sync エラー: %s", e)
            return None

        if not content:
            logger.warning("generate_response_sync: 空応答")
            return None

        import json as _json
        try:
            piece = _json.loads(content)
        except Exception as e:
            logger.warning("CCFolia JSON パース失敗: %s / raw=%s", e, content[:300])
            return None

        return _ccfolia_piece_to_flat_sheet(piece)

    def _generate_sheet_direct(
        self,
        rule: "RuleSystemAddon | None",
        concept_text: str,
        schema: type,
        temperature: float,
        max_tokens: int,
    ) -> dict | None:
        """ルール固有スキーマで直接シート辞書を生成する。"""
        assert self._lm_client is not None
        sys_prompt = (
            "あなたはTRPGのデータジェネレーターです。指定されたJSONスキーマに沿って"
            "キャラクターシートを生成してください。"
        )
        if rule is not None:
            override = rule.get_system_prompt_override()
            if override:
                sys_prompt = override + "\n\n" + sys_prompt

        user_message = (
            f"ユーザー要望: {concept_text}\n\n"
            "このTRPGシステムのルールに沿ったキャラクターシートを生成してください。"
        )
        try:
            result = self._lm_client.generate_structured_sync(
                system_prompt=sys_prompt,
                user_message=user_message,
                schema=schema,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=None,
            )
        except Exception as e:
            logger.error("generate_structured_sync エラー: %s", e)
            return None

        if result is None:
            return None
        try:
            return result.model_dump()
        except Exception:
            try:
                return dict(result)
            except Exception:
                return None

    def _generate_via_persona_builder(self, concept_text: str) -> CharacterBundle | None:
        """汎用パス: PersonaBuilder 経由でコンセプトからシートを生成する。"""
        import asyncio

        assert self._lm_client is not None
        from core.persona_builder import PersonaBuilder

        rule = self._active_rule_system()
        system_id = rule.manifest.id if (rule and getattr(rule, "manifest", None)) else "generic"
        builder = PersonaBuilder(self._lm_client, rule_system=system_id)
        try:
            result = asyncio.run(builder.build_from_concept(concept_text))
        except RuntimeError:
            # 既存イベントループ内での呼び出し時
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(builder.build_from_concept(concept_text))
            finally:
                loop.close()
        except Exception as e:
            logger.error("PersonaBuilder エラー: %s", e)
            return None

        if result is None:
            return None

        sheet = dict(result.character_json)
        persona = sheet.get("_persona", {})
        return CharacterBundle(
            sheet=sheet,
            roster_entry={"name": result.character_name, "description": result.persona_summary},
            persona=persona,
            system_prompt=result.system_prompt,
        )


# ──────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────


def _safe_filename(name: str) -> str:
    """保存ファイル名として使える文字のみ残す。"""
    return "".join(c for c in name if c.isalnum() or c in " _-（）").strip() or "unnamed"


def _ccfolia_piece_to_flat_sheet(piece: Any) -> dict | None:
    """CCFolia 形式 `{"kind":"character","data":{...}}` → フラットシート。

    AI が組んだ memo/commands/status/params はそのまま `_vtt_piece_raw` として
    保存し、flat フィールド（name/hp/sp/...）は status/params 配列から抽出する。
    """
    if not isinstance(piece, dict) or piece.get("kind") != "character":
        return None
    data = piece.get("data")
    if not isinstance(data, dict):
        return None

    status = data.get("status") or []
    params = data.get("params") or []

    def _pick_status(label: str, default: int = 0) -> int:
        for entry in status:
            if isinstance(entry, dict) and entry.get("label") == label:
                try:
                    return int(entry.get("value", default))
                except (TypeError, ValueError):
                    return default
        return default

    def _pick_param(label: str, default: int = 0) -> int:
        for entry in params:
            if isinstance(entry, dict) and entry.get("label") == label:
                try:
                    return int(entry.get("value", default))
                except (TypeError, ValueError):
                    return default
        return default

    memo_raw = data.get("memo", "") or ""
    alias = ""
    memo = memo_raw
    if memo_raw.startswith("【二つ名】"):
        first_line, sep, rest = memo_raw.partition("\n")
        alias = first_line.replace("【二つ名】", "").strip()
        memo = rest.lstrip("\n") if sep else ""

    return {
        "name": data.get("name", "名無し"),
        "alias": alias,
        "memo": memo,
        "hp": _pick_status("体力", 10),
        "sp": _pick_status("霊力", 10),
        "evasion": _pick_status("回避D", 2),
        "body": _pick_param("体", 3),
        "soul": _pick_param("霊", 3),
        "skill": _pick_param("巧", 3),
        "magic": _pick_param("術", 3),
        "mobility": _pick_param("機動力", 3),
        "armor": _pick_param("装甲", 0),
        "items": {
            "katashiro": _pick_status("形代", 1),
            "haraegushi": _pick_status("祓串", 0),
            "shimenawa": _pick_status("注連鋼縄", 0),
            "juryudan": _pick_status("呪瘤檀", 0),
            "ireikigu": _pick_status("医霊器具", 0),
            "meifuku": _pick_status("名伏", 0),
            "jutsuyen": _pick_status("術延起点", 0),
        },
        "_vtt_piece_raw": piece,
    }
