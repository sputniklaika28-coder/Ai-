# compress_tools.py
from llmlingua import PromptCompressor
import os

def run_compression():
    print("LLMLingua-2のモデルを準備しています...（初回はダウンロードに時間がかかります）")
    
    # LLMLingua-2のモデルを読み込む
    # ※LM-Studio（Qwen）にグラボを使わせるため、ここでは念のためCPUで動かします
    compressor = PromptCompressor(
        model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
        use_llmlingua2=True,
        device_map="cpu" 
    )

    # 1. 圧縮したい元の20万文字のデータを用意する
    # ※ここでは例として、ユーザーの要望や世界観テキストを手動で設定します
    # （実際のファイルパスに合わせて書き換えてください）
    print("元のテキストデータを読み込んでいます...")
    original_text = ""
    print("元のテキストデータを読み込んでいます...")
    
    # さっきコピペで作ったファイルを読み込む
    with open("world_data.txt", "r", encoding="utf-8") as f:
        original_text = f.read()

    print("圧縮を開始します...（PCの性能により数分〜数十分かかります）")
    # --- 以下の処理はそのまま ---

    print("圧縮を開始します...（PCの性能により数分〜数十分かかります）")
    
    # 2. 圧縮の実行（例：元のサイズの5%くらいまで圧縮するよう指示）
    results = compressor.compress_prompt(
        original_text,
        rate=0.05,
        force_tokens=['\n', '【', '】'] # ルールの見出しなど、絶対に消してほしくない記号
    )
    
    compressed_text = results['compressed_prompt']
    
    # 3. 圧縮した結果を「キャラメーカー専用ルール」として保存する
    output_path = "configs/char_maker_rules.txt"
    os.makedirs("configs", exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(compressed_text)
        
    print(f"圧縮完了！ {output_path} に保存しました！")
    print(f"元の文字数: {results['origin_tokens']} -> 圧縮後: {results['compressed_tokens']}")

if __name__ == "__main__":
    run_compression()