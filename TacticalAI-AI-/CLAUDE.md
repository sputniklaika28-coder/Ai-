# タクティカル祓魔師 TRPG AI — Claude Code ガイド

## プロジェクト概要

ローカル LLM（LM Studio）を使った TRPG ゲームマスター支援 AI システム。
ボードゲームサイト「ユドナリウム / ここフォリア」上でキャラクターの行動や戦術をリアルタイムに判断させる。

## ディレクトリ構成

```
TacticalAI/
├── core/
│   ├── main.py                  # PromptManager（プロンプトテンプレート管理）
│   ├── lm_client.py             # LMClient（LM Studio API ラッパー）
│   ├── session_manager.py       # SessionManager（ログ・バックアップ管理）
│   ├── character_manager.py     # CharacterManager（キャラクター管理）
│   ├── knowledge_manager.py     # KnowledgeManager（RAG + Web検索）
│   ├── ccfolia_map_controller.py # CCFoliaMapController（VTTアダプター委譲ラッパー）
│   ├── ccfolia_connector.py     # CCFolia連携（エージェントループ + チャット監視）
│   ├── vtt_adapters/
│   │   ├── base_adapter.py      # BaseVTTAdapter（VTT操作の抽象インターフェース）
│   │   └── ccfolia_adapter.py   # CCFoliaAdapter（Playwright sync_api 実装）
│   ├── char_maker.py            # キャラクター作成ユーティリティ
│   ├── gui_tool.py              # tkinter GUI 補助ツール
│   └── launcher.py              # メインランチャー（tkinter GUI）
├── configs/
│   ├── characters.json          # キャラクター定義（最大15体）
│   ├── prompts.json             # プロンプトテンプレート集
│   ├── session_config.json      # セッション設定
│   ├── world_setting.json       # 世界観設定
│   ├── board_state.json         # ボード状態キャッシュ
│   └── map_commands.json        # マップコマンド定義
├── data/
│   └── chroma_db/               # ChromaDB ベクトルDB（.gitignore対象）
├── sessions/                    # セッションログ保存先（自動生成）
├── tests/                       # pytest テストスイート
├── pyproject.toml               # ツール設定（pytest / ruff / mypy）
├── run.bat                      # Windows 起動スクリプト
├── ai_compressor..py            # 世界観テキスト AI 圧縮ツール
└── sv_to_txt.py                 # セーブデータ→テキスト変換
```

## 主要クラスと責務

### `LMClient` (core/lm_client.py)
- LM Studio の OpenAI 互換 API（`http://localhost:1234`）に POST
- `generate_response()` が基本メソッド。返答を `_clean_response()` で JSON 抽出
- `generate_with_tools()` がツール呼び出し対応のマルチターン推論メソッド（画像入力対応）
- `no_think=True` で Qwen3 系の思考トークンを抑制
- テスト時は `requests.post` / `requests.get` をモックすること

### `PromptManager` (core/main.py)
- `configs/prompts.json` からテンプレートを読み込む
- `get_template(id)` / `update_template(id, data)` で CRUD
- テスト時は `tmp_path` fixture で一時 JSON ファイルを使うこと

### `SessionManager` (core/session_manager.py)
- `sessions/<timestamp>_<name>/` フォルダにセッションを記録
- ログは JSONL 形式（1行1 JSON オブジェクト）
- `start_new_session()` → `log_message()` → ファイルに追記

### `CharacterManager` (core/character_manager.py)
- `configs/characters.json` でキャラクターを管理
- `get_enabled_characters()` で `enabled: true` のキャラのみ返す

### `CCFoliaMapController` (core/ccfolia_map_controller.py)
- VTTアダプター（`BaseVTTAdapter`）に駒操作を委譲するラッパー
- `get_board_state()` → `move_piece(img_hash, grid_x, grid_y)` → `spawn_piece(character_json)`
- テスト時は `MagicMock` で `adapter` を差し替えること

### `BaseVTTAdapter` / `CCFoliaAdapter` (core/vtt_adapters/)
- `BaseVTTAdapter`: VTT操作の抽象インターフェース（同期API）
- `CCFoliaAdapter`: Playwright sync_api による CCFolia 操作実装
- JSベースのドラッグ操作、クリップボードペーストによる駒配置
- テスト時は `MagicMock` で Playwright の `page` を差し替えること

### `KnowledgeManager` (core/knowledge_manager.py)
- ChromaDB によるベクトル検索（ルールブック、セッションログ等）
- DuckDuckGo Search によるウェブ検索
- `search_knowledge_base(query)` / `search_web(query)` が主要メソッド
- `ingest_world_setting()` / `ingest_session_log()` でデータ取り込み
- テスト時は `tmp_path` で一時 ChromaDB を使うこと

## 開発コマンド

```bash
# テスト実行
uv run pytest tests/ -v

# Lint / Format
uv run ruff check core/ tests/
uv run ruff format core/ tests/

# 型チェック
uv run mypy core/ --ignore-missing-imports

# アプリ起動（Windows）
run.bat
```

## テスト設計方針

- **外部依存はすべてモック**: LM Studio API（requests）、Selenium WebDriver、tkinter
- `conftest.py` に共通フィクスチャ（tmp_path ベースの JSON、mock LMClient）を集約
- `ccfolia_map_controller` は `MagicMock(driver)` で DOM 操作を模倣
- GUI（tkinter）テストは対象外とする

## 設定ファイルスキーマ

### characters.json
```json
{
  "characters": {
    "<id>": {
      "id": "string",
      "name": "string",
      "layer": "meta | setting | player",
      "role": "game_master | npc_manager | player",
      "enabled": true,
      "is_ai": true,
      "prompt_id": "string"
    }
  }
}
```

### prompts.json
```json
{
  "templates": {
    "<template_id>": {
      "system": "string",
      "user_template": "string"
    }
  }
}
```

## 注意事項

- Python 3.12+ を前提（`list[dict]` 等の新構文あり）
- `core/__pycache__` は `.gitignore` で除外
- `configs/enemy_pieces.json` はセンシティブ情報を含む可能性があるため `.gitignore` で除外（`.example` のみ管理）
- `sessions/` フォルダはセッションログ（`.gitignore` 対象）
- `LMClient` の `generate_response` はサーバー未起動時に `(None, None)` を返す仕様
