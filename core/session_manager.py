# ================================
# ファイル: core/session_manager.py
# セッションのログ保存とバックアップを管理 (JSONL対応版)
# ================================

import json
import shutil
from datetime import datetime
from pathlib import Path


class SessionManager:
    def __init__(self, base_dir: Path):
        """
        base_dir: TacticalAI のルートディレクトリ
        """
        self.base_dir = Path(base_dir)
        self.sessions_dir = self.base_dir / "sessions"
        self.configs_dir = self.base_dir / "configs"

        # セッションごとの情報を保持
        self.current_session_dir: Path | None = None
        self.log_file: Path | None = None
        self.history: list[dict] = []

    def start_new_session(self, session_name: str = "UnnamedSession"):
        """新しいセッションを開始し、フォルダとバックアップを作成する"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join([c for c in session_name if c.isalnum() or c in " _-"]).strip()
        folder_name = f"{timestamp}_{safe_name}"

        self.current_session_dir = self.sessions_dir / folder_name
        self.current_session_dir.mkdir(parents=True, exist_ok=True)

        # JSONL形式（1行1オブジェクト）に変更
        self.log_file = self.current_session_dir / "chat_log.jsonl"

        print(f"📁 セッションを開始します: {folder_name}")

        self.history = []
        # 新規作成時は空のファイルを作る
        if self.log_file:
            self.log_file.touch(exist_ok=True)

        self._backup_configs()

    def _backup_configs(self):
        """現在の configs フォルダの中身をセッションフォルダにコピーする"""
        if not self.configs_dir.exists():
            return

        backup_dir = self.current_session_dir / "configs_backup"
        shutil.copytree(self.configs_dir, backup_dir, dirs_exist_ok=True)
        print("   ✓ 設定ファイルのバックアップを保存しました")

    def log_message(self, speaker: str, body: str):
        """チャットメッセージをJSONL形式で追記記録する"""
        if not self.log_file:
            return

        msg_data = {"timestamp": datetime.now().isoformat(), "speaker": speaker, "body": body}
        self.history.append(msg_data)

        # 追記モード('a')で1行だけ書き込む
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg_data, ensure_ascii=False) + "\n")

    def load_session(self, session_folder_name: str) -> bool:
        """既存のセッション(JSONL)を読み込んで再開する"""
        target_dir = self.sessions_dir / session_folder_name
        log_path = target_dir / "chat_log.jsonl"

        if not log_path.exists():
            print(f"⚠️ 指定されたセッションまたはログが見つかりません: {session_folder_name}")
            return False

        self.current_session_dir = target_dir
        self.log_file = log_path
        self.history = []

        try:
            # JSONLを1行ずつ読み込む
            with open(self.log_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.history.append(json.loads(line))
            print(f"📁 セッションを再開します: {session_folder_name} ({len(self.history)}件のログ)")
            return True
        except json.JSONDecodeError:
            print("⚠️ ログファイルの読み込み中にエラーが発生しました")
            return False
