#!/bin/bash
cd "$(dirname "$0")"

# customtkinter が未インストールなら自動インストール
python -c "import customtkinter" 2>/dev/null || pip install customtkinter

python core/launcher_v2.py
