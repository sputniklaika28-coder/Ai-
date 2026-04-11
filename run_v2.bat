@echo off
cd /d "%~dp0"

:: customtkinter が未インストールなら自動インストール
python -c "import customtkinter" 2>nul || (
    echo customtkinter をインストールしています...
    pip install customtkinter
)

python core/launcher_v2.py
pause
