# ai_compressor.py
# 任意のテキスト（世界観、シナリオPC情報など）をAIで高密度圧縮するツール
import json
import requests
import os

# LM-Studioのローカルサーバー設定
LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"

def compress_text(input_filepath, output_filepath):
    print(f"\n📄 ファイルを読み込んでいます: {input_filepath}")
    try:
        with open(input_filepath, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except FileNotFoundError:
        print("❌ ファイルが見つかりません。パスを確認してください。")
        return

    # AIに「TRPGのシステム用に圧縮しろ」と指示するプロンプト
    system_prompt = (
        "あなたはTRPGのデータ圧縮アルゴリズムです。\n"
        "入力されたテキストから『世界観の要点』『ルール・数値』『固有名詞』『シナリオの重要情報』を一切欠落させずに、"
        "無駄な装飾語や重複表現だけを削ぎ落とし、箇条書きや独自DSLのような高密度なテキストに圧縮・要約してください。\n"
        "※出力は圧縮されたテキストデータのみとし、挨拶や解説は絶対に含めないでください。"
    )

    payload = {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下のテキストを高密度に圧縮してください：\n\n{raw_text}"}
        ],
        "temperature": 0.2, # 創造性を消し、正確に要約させる
        "max_tokens": 4000
    }

    print("🤖 AIに圧縮処理を依頼中...（数十秒〜数分かかります）")
    try:
        response = requests.post(LM_STUDIO_URL, json=payload, timeout=600)
        response.raise_for_status()
        result_text = response.json()["choices"][0]["message"]["content"].strip()
        
        # 保存先のフォルダが存在しない場合は作成
        os.makedirs(os.path.dirname(output_filepath), exist_ok=True)
        
        with open(output_filepath, "w", encoding="utf-8") as f:
            f.write(result_text)
            
        print(f"✅ 圧縮完了！ {output_filepath} に保存しました。")
        print(f"📊 文字数変化: {len(raw_text)}文字 ➡️ {len(result_text)}文字\n")
        
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")

if __name__ == "__main__":
    print("========================================")
    print(" 🗜️ TRPGデータ AI高密度圧縮ツール")
    print("========================================")
    print("圧縮したいテキストファイル（未圧縮データ）のパスを入力してください。")
    print("例: raw_scenario.txt または world_setting.txt など")
    
    in_path = input("▶ 入力ファイル: ").strip()
    out_path = input("▶ 保存先ファイル名 (例: configs/compressed_scenario.txt): ").strip()
    
    if in_path and out_path:
        compress_text(in_path, out_path)