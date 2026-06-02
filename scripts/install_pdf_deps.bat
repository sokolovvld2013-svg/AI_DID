@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist "venv\Scripts\pip.exe" (
    echo Сначала создайте venv: python -m venv venv
    pause
    exit /b 1
)
echo Установка Python-пакетов для чтения PDF (модуль Юрист)...
venv\Scripts\pip.exe install -r requirements.txt
venv\Scripts\python.exe -c "import fitz; import pypdfium2; from rapidocr_onnxruntime import RapidOCR; print('OK: pymupdf, pypdfium2, rapidocr')"
echo.
echo Перезапустите сервер uvicorn и снова загрузите PDF.
pause
