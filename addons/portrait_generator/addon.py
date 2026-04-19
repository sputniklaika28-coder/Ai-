"""addon.py — 立ち絵・トークン自動生成 + ペルソナ構築アドオン (Phase 2)。

ツール:
  build_character_persona  — コンセプトテキスト → キャラクターJSON + システムプロンプト
  generate_character_portrait — キャラクター名 → 立ち絵PNG + トークンPNG
  generate_npc_persona     — NPC説明 → NPCペルソナ定義

依存アドオン: image_generator（ComfyUI クライアント提供）
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 保存ファイル名として許可する記号（英数字以外）
_SAFE_FILENAME_EXTRA_CHARS = "-_（）"

try:
    from core.addons.addon_base import AddonBase, AddonContext, ToolExecutionContext
    from core.persona_builder import PersonaBuilder, PersonaBuildResult
except ModuleNotFoundError:
    from addons.addon_base import AddonBase, AddonContext, ToolExecutionContext  # type: ignore
    from persona_builder import PersonaBuilder, PersonaBuildResult  # type: ignore


class PortraitGeneratorAddon(AddonBase):
    """立ち絵・トークン自動生成 + ペルソナ自動構築アドオン。

    on_load 時に PersonaBuilder と PortraitPipeline を初期化する。
    PortraitPipeline は image_generator アドオンの ComfyUIClient を借用する。
    """

    def __init__(self) -> None:
        self._persona_builder: PersonaBuilder | None = None
        self._pipeline: Any | None = None  # PortraitPipeline
        self._client: Any | None = None  # ComfyUIClient
        self._output_dir: Path | None = None
        self._lm_client: Any | None = None
        self._context: AddonContext | None = None

    # ──────────────────────────────────────
    # ライフサイクル
    # ──────────────────────────────────────

    def on_load(self, context: AddonContext) -> None:
        self._context = context
        self._lm_client = context.lm_client
        self._output_dir = self._resolve_output_dir(context)
        self._persona_builder = PersonaBuilder(context.lm_client)

        # ComfyUI 未起動でもロードは成功させ、実行時に再試行する
        self._try_init_pipeline(context)

        logger.info(
            "PortraitGeneratorAddon: ロード完了 (pipeline=%s)",
            "有効" if self._pipeline else "遅延初期化(ComfyUI未検出)",
        )

    @staticmethod
    def _resolve_output_dir(context: AddonContext) -> Path:
        """PORTRAIT_OUTPUT_DIR を考慮して出力先を決定する。"""
        try:
            from core.config import get_portrait_output_dir
            raw = get_portrait_output_dir()
        except ImportError:
            raw = "generated_images"
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = context.root_dir / candidate
        return candidate

    def _import_pipeline_classes(self) -> tuple[Any, Any, Any] | None:
        """image_generator アドオンのクラス群を取得する。"""
        try:
            from addons.image_generator.comfyui_client import ComfyUIClient, ComfyUIConfig
            from addons.image_generator.portrait_pipeline import PortraitPipeline
            return ComfyUIClient, ComfyUIConfig, PortraitPipeline
        except ImportError as e:
            logger.warning("PortraitPipeline 初期化失敗 (image_generator 未導入): %s", e)
            return None

    def _try_init_pipeline(self, context: AddonContext) -> Any | None:
        """クライアント＋パイプラインを準備。ComfyUI 未起動時はクライアントだけ保持。"""
        classes = self._import_pipeline_classes()
        if classes is None:
            return None
        ComfyUIClient, ComfyUIConfig, PortraitPipeline = classes

        if self._client is None:
            self._client = ComfyUIClient(ComfyUIConfig.from_env())

        if not self._client.is_available():
            logger.info("PortraitGeneratorAddon: ComfyUI 未起動 → 呼び出し時に再試行")
            self._pipeline = None
            return None

        if self._pipeline is None:
            self._pipeline = PortraitPipeline(
                comfyui_client=self._client,
                output_dir=self._output_dir or Path("generated_images"),
            )
        return self._pipeline

    def _get_pipeline(self) -> Any | None:
        """実行時に遅延初期化＋再試行するためのアクセサ。"""
        ctx = self._context
        if ctx is None:
            return self._pipeline
        if self._pipeline is not None:
            # 既存パイプラインは流用。ComfyUI の再起動に備え is_available を軽く確認
            if self._client is not None and self._client.is_available():
                return self._pipeline
            # 応答しなくなった場合は捨てる
            self._pipeline = None
        return self._try_init_pipeline(ctx)

    def on_unload(self) -> None:
        self._persona_builder = None
        self._pipeline = None

    # ──────────────────────────────────────
    # ツール定義
    # ──────────────────────────────────────

    def get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "build_character_persona",
                    "description": (
                        "プレイヤーのキャラクターコンセプトテキストから、"
                        "キャラクターシートJSON とシステムプロンプトを自動生成する。"
                        "キャラクター作成フェーズで使用する。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "concept_text": {
                                "type": "string",
                                "description": (
                                    "キャラクターのコンセプト（例: '射撃が得意な無口な少女祓魔師'）"
                                ),
                            },
                            "player_name": {
                                "type": "string",
                                "description": "プレイヤー名（省略可）",
                            },
                            "save_character": {
                                "type": "boolean",
                                "description": "生成したキャラクターを configs/saved_pcs/ に保存するか",
                            },
                        },
                        "required": ["concept_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_character_portrait",
                    "description": (
                        "キャラクターの立ち絵とVTTトークン（円形PNG）を自動生成する。"
                        "saved_pcs 未保存のキャラクターでも description/keywords を直接渡せる。"
                        "ComfyUI が起動している必要がある。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_name": {
                                "type": "string",
                                "description": "生成対象のキャラクター名（保存されていればそれを使用、無ければ description から生成）",
                            },
                            "description": {
                                "type": "string",
                                "description": "外見・特徴の説明文（未保存キャラクターの場合に使用。日本語可）",
                            },
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "追加の英語キーワードリスト（省略可）",
                            },
                            "style": {
                                "type": "string",
                                "enum": [
                                    "anime_character",
                                    "fantasy_portrait",
                                    "dark_gothic",
                                    "token_simple",
                                ],
                                "description": "生成スタイル（省略時: anime_character）",
                            },
                            "extra_keywords": {
                                "type": "string",
                                "description": "追加の英語プロンプト文字列（カンマ区切り、省略可）",
                            },
                        },
                        "required": ["character_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "generate_npc_persona",
                    "description": (
                        "NPC の説明からペルソナ（システムプロンプト）を自動生成する。"
                        "GM がシーン中に新しい NPC を追加する際に使用する。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "npc_description": {
                                "type": "string",
                                "description": (
                                    "NPC の説明（例: '老齢の神社神主、温厚だが秘密を抱える'）"
                                ),
                            },
                            "relationship_to_party": {
                                "type": "string",
                                "description": "パーティとの関係（例: '情報提供者', '敵'）",
                            },
                        },
                        "required": ["npc_description"],
                    },
                },
            },
        ]

    # ──────────────────────────────────────
    # ツール実行
    # ──────────────────────────────────────

    def execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        context: ToolExecutionContext,
    ) -> tuple[bool, str | None]:
        if tool_name == "build_character_persona":
            return self._execute_build_persona(tool_args, context)
        if tool_name == "generate_character_portrait":
            return self._execute_generate_portrait(tool_args, context)
        if tool_name == "generate_npc_persona":
            return self._execute_npc_persona(tool_args, context)
        return False, json.dumps({"error": f"未知のツール: {tool_name}"}, ensure_ascii=False)

    def _run_async(self, coro: Any) -> Any:
        """コルーチンを同期コンテキストから実行する。"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result(timeout=120)
            return loop.run_until_complete(coro)
        except Exception:
            return asyncio.run(coro)

    def _execute_build_persona(
        self,
        args: dict,
        context: ToolExecutionContext,
    ) -> tuple[bool, str]:
        if self._persona_builder is None:
            return False, json.dumps({"error": "PersonaBuilder が初期化されていません"}, ensure_ascii=False)

        concept_text = args.get("concept_text", "")
        player_name = args.get("player_name", "")
        should_save = args.get("save_character", True)

        if not concept_text.strip():
            return False, json.dumps({"error": "concept_text が空です"}, ensure_ascii=False)

        result: PersonaBuildResult | None = self._run_async(
            self._persona_builder.build_from_concept(
                concept_text=concept_text,
                player_name=player_name,
            )
        )

        if result is None:
            return False, json.dumps({"error": "キャラクター生成に失敗しました"}, ensure_ascii=False)

        # キャラクターを保存
        if should_save:
            self._save_character(result.character_name, result.character_json, context)

        return True, json.dumps({
            "character_name": result.character_name,
            "persona_summary": result.persona_summary,
            "system_prompt": result.system_prompt,
            "speech_examples": result.speech_style_examples,
            "portrait_keywords": result.portrait_keywords,
            "character_json": result.character_json,
            "saved": should_save,
        }, ensure_ascii=False, indent=2)

    def _execute_generate_portrait(
        self,
        args: dict,
        context: ToolExecutionContext,
    ) -> tuple[bool, str]:
        pipeline = self._get_pipeline()
        if pipeline is None:
            return False, json.dumps({
                "ok": False,
                "error": "ComfyUI に接続できません。起動しているか .env の COMFYUI_HOST/COMFYUI_PORT を確認してください。",
                "error_code": "comfyui_unavailable",
            }, ensure_ascii=False)

        character_name = (args.get("character_name") or "").strip()
        style = args.get("style", "anime_character")
        extra_keywords = args.get("extra_keywords", "") or ""
        description = (args.get("description") or "").strip()
        explicit_keywords = args.get("keywords") or []
        if isinstance(explicit_keywords, str):
            explicit_keywords = [k.strip() for k in explicit_keywords.split(",") if k.strip()]

        # saved_pcs から優先的にロード
        char_json = self._load_character(character_name, context) if character_name else None

        # 未保存なら description / keywords から最小限の char_json を構築
        if char_json is None:
            if not (description or explicit_keywords):
                return False, json.dumps({
                    "ok": False,
                    "error": (
                        f"キャラクター '{character_name}' が見つかりません。"
                        "保存済みでない場合は description または keywords を渡してください。"
                    ),
                    "error_code": "character_not_found",
                }, ensure_ascii=False)
            fallback_name = character_name or "character"
            char_json = {
                "name": fallback_name,
                "alias": "",
                "_persona": {
                    "portrait_keywords": list(explicit_keywords),
                    "description": description,
                },
            }
            character_name = fallback_name

        persona = char_json.setdefault("_persona", {})
        keywords = list(persona.get("portrait_keywords") or [])
        # 明示キーワードを優先し重複除去
        for kw in explicit_keywords:
            if kw and kw not in keywords:
                keywords.append(kw)
        if not keywords:
            keywords = [character_name] if character_name else ["character"]

        # description はプロンプト末尾に追加（短文なら）
        extra_positive_parts = []
        if extra_keywords:
            extra_positive_parts.append(extra_keywords)
        if description:
            extra_positive_parts.append(description)
        extra_positive = ", ".join(extra_positive_parts)

        result = pipeline.generate_portrait(
            character_name=character_name,
            portrait_keywords=keywords,
            style=style,
            remove_bg=True,
            create_token=True,
            extra_positive=extra_positive,
        )

        if not result.success:
            return False, json.dumps({
                "ok": False,
                "error": result.error or "画像生成失敗",
                "error_code": "generation_failed",
            }, ensure_ascii=False)

        return True, json.dumps({
            "ok": True,
            "character_name": character_name,
            "portrait_path": result.portrait_path,
            "image_path": result.portrait_path,  # launcher 互換エイリアス
            "token_path": result.token_path,
            "raw_path": result.raw_path,
            "background_removed": result.background_removed,
            "elapsed_seconds": round(result.elapsed_seconds, 1),
        }, ensure_ascii=False, indent=2)

    def _execute_npc_persona(
        self,
        args: dict,
        context: ToolExecutionContext,
    ) -> tuple[bool, str]:
        if self._persona_builder is None:
            return False, json.dumps({"error": "PersonaBuilder が初期化されていません"}, ensure_ascii=False)

        npc_description = args.get("npc_description", "")
        relationship = args.get("relationship_to_party", "")

        result: PersonaBuildResult | None = self._run_async(
            self._persona_builder.build_npc_persona(
                npc_description=npc_description,
                relationship_to_party=relationship,
            )
        )

        if result is None:
            return False, json.dumps({"error": "NPC ペルソナ生成に失敗しました"}, ensure_ascii=False)

        return True, json.dumps({
            "npc_name": result.character_name,
            "persona_summary": result.persona_summary,
            "system_prompt": result.system_prompt,
            "speech_examples": result.speech_style_examples,
        }, ensure_ascii=False, indent=2)

    # ──────────────────────────────────────
    # ヘルパー: キャラクター保存/ロード
    # ──────────────────────────────────────

    def _save_character(
        self,
        name: str,
        char_json: dict,
        context: ToolExecutionContext,
    ) -> None:
        """キャラクターを saved_pcs/ ディレクトリに保存する。"""
        try:
            connector = context.connector
            char_manager = getattr(connector, "char_manager", None) or getattr(
                connector, "character_manager", None
            )
            if char_manager and hasattr(char_manager, "save_character"):
                char_manager.save_character(name, char_json)
                logger.info("_save_character: '%s' を保存", name)
                return
        except Exception as e:
            logger.warning("_save_character: char_manager 経由の保存失敗: %s", e)

        # フォールバック: 直接ファイル書き込み
        try:
            root = Path(__file__).resolve().parent.parent.parent
            save_dir = root / "configs" / "saved_pcs"
            save_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(
                c if c.isalnum() or c in _SAFE_FILENAME_EXTRA_CHARS else "_" for c in name
            )
            save_path = save_dir / f"{safe_name}.json"
            save_path.write_text(
                json.dumps(char_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("_save_character: '%s' を %s に保存", name, save_path)
        except Exception as e:
            logger.error("_save_character: 保存失敗: %s", e)

    def _load_character(
        self,
        name: str,
        context: ToolExecutionContext,
    ) -> dict | None:
        """saved_pcs/ からキャラクターデータをロードする。"""
        root = Path(__file__).resolve().parent.parent.parent
        save_dir = root / "configs" / "saved_pcs"

        # 完全一致
        for ext in [".json"]:
            path = save_dir / f"{name}{ext}"
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass

        # 部分一致
        for path in save_dir.glob("*.json"):
            if name.lower() in path.stem.lower():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass

        return None
