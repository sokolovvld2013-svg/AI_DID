@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist venv\Scripts\python.exe (
    echo Сначала создайте venv и установите зависимости: pip install -r requirements.txt
    pause
    exit /b 1
)
set HF_ENDPOINT=https://hf-mirror.com
set HF_HUB_DOWNLOAD_TIMEOUT=300
echo Зеркало HuggingFace: %HF_ENDPOINT%
venv\Scripts\python.exe scripts\download_embedding_model.py
pause
