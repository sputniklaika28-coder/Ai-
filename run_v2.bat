@echo off
cd /d "%~dp0"
pip install customtkinter --quiet
python core/launcher_v2.py
pause
