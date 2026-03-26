import csv
import sys
from pathlib import Path

def convert_csv_to_markdown(csv_path: str):
    input_file = Path(csv_path)
    if not input_file.exists():
        print(f"❌ エラー: '{input_file}' が見つかりません。")
        return

    # 出力ファイル名（元のファイル名.txt になります）
    output_file = input_file.with_suffix('.txt')

    try:
        with open(input_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            headers = next(reader) # 1行目を項目名（ヘッダー）として取得

            with open(output_file, 'w', encoding='utf-8') as out:
                out.write(f"# {input_file.stem} データベース\n\n")

                for row in reader:
                    # 空行はスキップ
                    if not any(row): continue
                    
                    # 1列目（A列）を「名前」として扱う
                    name = row[0].strip()
                    if not name: continue

                    out.write(f"## 【{name}】\n")
                    
                    # 2列目以降を箇条書きで書き出す
                    for i in range(1, len(headers)):
                        if i < len(row) and row[i].strip():
                            header_name = headers[i].strip()
                            # セル内の改行をMarkdownのインデントに合わせて綺麗に整形
                            val = row[i].strip().replace('\n', '\n  ')
                            out.write(f"- **{header_name}**: {val}\n")
                    
                    out.write("\n")

        print(f"✨ 変換成功！: {output_file} を作成しました！")
        print(f"このファイルを configs/database/ フォルダに入れてください。")

    except Exception as e:
        print(f"❌ 変換中にエラーが発生しました: {str(e)}")

if __name__ == "__main__":
    print("=" * 50)
    print(" 🛠️ スプレッドシート(CSV) → AI用テキスト 爆速変換ツール")
    print("=" * 50)
    csv_input = input("▶ ダウンロードしたCSVファイルのパスをドラッグ＆ドロップしてください:\n").strip('\"\'')
    
    if csv_input:
        convert_csv_to_markdown(csv_input)
    
    input("\nEnterキーを押して終了します...")