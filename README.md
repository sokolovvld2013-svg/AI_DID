# ИИ-помощник ФГУП "ДИД"

Веб-приложение с тремя модулями: **Экономист**, **Секретарь**, **Юрист**.

## Стек

- Backend: Python 3.11+, FastAPI
- Frontend: HTML, CSS, JavaScript
- LLM: GigaChat или DeepSeek (переключение в `.env`)
- RAG: ChromaDB + локальные эмбеддинги (sentence-transformers)
- Транскрибация: faster-whisper

## Установка

```bash
cd "d:\Нужные файлы\Программирование\Python\ДИД_ассистент"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Скопируйте `.env` и укажите API-ключи:

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=ваш_ключ
# или для GigaChat:
# LLM_PROVIDER=gigachat
# GIGACHAT_CREDENTIALS=ваш_ключ
```

## Первый запуск

При первом запуске:

1. **Whisper** — по умолчанию модель `base` (меньше и быстрее `small` на CPU); скачается автоматически.
2. **Эмбеддинги** — модель `paraphrase-multilingual-MiniLM-L12-v2` (~400 МБ). Если `huggingface.co` недоступен:
   - быстро: `EMBEDDING_PROVIDER=openai` в `.env` (нужен `OPENAI_API_KEY`);
   - офлайн: `scripts\download_embedding_model.bat`, затем `EMBEDDING_LOCAL_FILES_ONLY=1`;
   - зеркало: `HF_ENDPOINT=https://hf-mirror.com`, `HF_HUB_DOWNLOAD_TIMEOUT=300`.
3. Создадутся папки `economist/uploaded`, `secretary/uploaded`, `lawyer/uploaded`, `chroma_data`.

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Откройте в браузере: http://localhost:8000

## Модули

### Экономист
- **Таблица факта** — кнопка на странице (`ECONOMIST_FACT_SHEET_URL` в `.env`)
- **Чат** — запрос уходит в **n8n**, ответ показывается в чате (`N8N_ECONOMIST_WEBHOOK_URL`)
- Настройка n8n: см. [docs/N8N_ECONOMIST.md](docs/N8N_ECONOMIST.md)

### Секретарь
- Загрузите аудио (.mp3, .wav, .m4a, до 100 МБ)
- Получите протокол совещания (транскрипт + LLM)

### Юрист
- Загрузите DOCX, TXT; PDF поддерживается, поиск по PDF менее эффективен (до 50 МБ, до 200 стр.)
- Задавайте вопросы — ответы с цитатами и подсветкой
- **Распознавание только средствами Python** (`pip install -r requirements.txt`):
  - текстовые PDF: `pymupdf`, `pypdfium2`, `pdfplumber`, `pypdf`
  - PDF-сканы (картинки): `rapidocr-onnxruntime` + `opencv-python-headless`
- На сервере **не нужны** Tesseract, LibreOffice, MS Word, Poppler

## GitHub

Перед публикацией: не коммитьте `.env` (ключи API). Используйте `.env.example` как шаблон.

Подробно: [docs/GITHUB.md](docs/GITHUB.md)

```bash
git init
git add .
git status   # убедитесь, что нет .env и venv/
git commit -m "Initial commit"
git remote add origin https://github.com/USER/REPO.git
git push -u origin main
```

## Развёртывание на сервере

1. Python 3.11+ и виртуальное окружение:
   ```bash
   python3 -m venv venv
   source venv/bin/activate   # Linux
   pip install -r requirements.txt
   ```
2. Скопируйте `.env` с ключами API, `N8N_ECONOMIST_WEBHOOK_URL` и т.д.
3. Запуск (пример):
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```
4. Для офлайн-эмбеддингов заранее скачайте модель в `models/` или укажите `EMBEDDING_PROVIDER=openai`.
5. Первый запуск RapidOCR скачает ONNX-модели (~десятки МБ) — учтите при деплое.
6. **Секретарь (Whisper):** `pip install faster-whisper` и системный `ffmpeg` (`sudo apt install -y ffmpeg`). Первый запуск скачает модель (~150 МБ для `base`). На слабом VPS: `WHISPER_MODEL_SIZE=base`, `WHISPER_BEAM_SIZE=1`; при старте можно `WHISPER_PRELOAD=true`.

6. **OCR: процесс `Killed`** — не хватает оперативной памяти на VPS. В `.env` на сервере:
   ```env
   LAWYER_OCR_SCALE=1.0
   LAWYER_OCR_MAX_SIDE=1200
   ```
   Либо добавьте swap (`fallocate -l 2G /swapfile && ...`), либо загружайте **DOCX** вместо скан-PDF.

7. **OCR на Linux (ошибка `libGL.so.1`)** — часто ставится лишний `opencv-python`. Исправление:
   ```bash
   source venv/bin/activate
   pip uninstall -y opencv-python
   pip install --force-reinstall opencv-python-headless
   bash scripts/fix_opencv_server.sh
   ```
   Либо системный пакет (если headless не помог): `sudo apt install -y libgl1 libglib2.0-0`

Внешние программы для модуля Юрист **не используются** — только пакеты из `requirements.txt`.

## Конфигурация

| Переменная | Описание |
|------------|----------|
| `LLM_PROVIDER` | `gigachat` или `deepseek` |
| `EMBEDDING_PROVIDER` | `local` или `openai` |
| `HF_ENDPOINT` | зеркало HuggingFace, напр. `https://hf-mirror.com` |
| `EMBEDDING_LOCAL_FILES_ONLY` | `1` — не обращаться к huggingface.co |
| `LOCAL_EMBEDDING_MODEL` | путь `models/paraphrase-multilingual-MiniLM-L12-v2` |
| `WHISPER_MODEL_SIZE` | `tiny`, `base` (по умолчанию), `small`, `medium`, `large-v3` |
| `WHISPER_BEAM_SIZE` | `1` — быстро на CPU; `5` — точнее, медленнее |
| `WHISPER_CPU_THREADS` | `0` = все ядра; или число потоков |
| `WHISPER_PRELOAD` | `true` — загрузить модель при старте uvicorn |
| `MAX_EXCEL_SIZE` | лимит Excel (байты) |
| `MAX_AUDIO_SIZE` | лимит аудио |
| `MAX_DOCUMENT_SIZE` | лимит документов |
| `ECONOMIST_FACT_SHEET_URL` | ссылка на таблицу факта (`/edit`) |
| `N8N_ECONOMIST_WEBHOOK_URL` | Production URL webhook n8n для чата |
| `N8N_ECONOMIST_TIMEOUT` | таймаут ответа n8n, сек (по умолчанию 120) |
| `LAWYER_CITATION_LLM_REPAIR` | `true` — восстанавливать русский текст в блоке «Источники» через LLM при искажениях PDF/OCR |

## Структура

```
├── economist/     # план/факт Excel, RAG
├── secretary/     # аудио, Whisper, протоколы
├── lawyer/        # документы, RAG, цитаты
├── core/          # LLM, эмбеддинги, история
├── static/        # CSS, JS
├── templates/     # HTML
├── main.py
└── config.py
```

## Примечания

- История запросов хранится в памяти и сбрасывается при перезапуске сервера.
- Для GPU-транскрибации: `WHISPER_DEVICE=cuda`, `WHISPER_COMPUTE_TYPE=float16`, можно `WHISPER_MODEL_SIZE=small`.
- После смены `WHISPER_MODEL_SIZE` перезапустите uvicorn (модель кэшируется в памяти процесса).
- Без API-ключей LLM-модули (Секретарь, Юрист) не смогут формировать ответы; Экономист работает без LLM для расчётов.
