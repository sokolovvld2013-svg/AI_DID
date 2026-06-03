#!/bin/bash
# OpenCV на Linux-сервере: только headless (без libGL / дисплея).
set -e
cd "$(dirname "$0")/.."
source venv/bin/activate 2>/dev/null || true

echo "Удаляем opencv-python (требует libGL)..."
pip uninstall -y opencv-python 2>/dev/null || true

echo "Ставим opencv-python-headless и OCR..."
pip install --upgrade opencv-python-headless rapidocr-onnxruntime onnxruntime

python -c "
import cv2
from rapidocr_onnxruntime import RapidOCR
RapidOCR()
print('OCR OK, cv2:', cv2.__file__)
"
