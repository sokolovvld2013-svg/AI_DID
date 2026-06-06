# ИИ-помощник ФГУП "ДИД"

Веб-приложение с тремя модулями: **Экономист**, **Секретарь**, **Юрист**.

## Стек

- Backend: Python 3.11+, FastAPI
- Frontend: HTML, CSS, JavaScript
- LLM: GigaChat или DeepSeek (переключение в `.env`)
- RAG (Юрист): ChromaDB + локальные эмбеддинги (sentence-transformers)
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
2. **Эмбеддинги** — по умолчанию локальная модель `paraphrase-multilingual-MiniLM-L12-v2` (~400 МБ). Если `huggingface.co` недоступен:
   - быстро: `EMBEDDING_PROVIDER=openai` в `.env` (нужен `OPENAI_API_KEY`);
   - эксперимент: `EMBEDDING_PROVIDER=gigachat` (нужен `GIGACHAT_CREDENTIALS`, модель `GIGACHAT_EMBEDDING_MODEL=Embeddings`);
   - офлайн: `scripts\download_embedding_model.bat`, затем `EMBEDDING_LOCAL_FILES_ONLY=1`;
   - зеркало: `HF_ENDPOINT=https://hf-mirror.com`, `HF_HUB_DOWNLOAD_TIMEOUT=300`.

   При смене `EMBEDDING_PROVIDER` или модели эмбеддингов векторы в Chroma несовместимы — очистите индекс Юриста (кнопка в интерфейсе или `DELETE /lawyer/index`) и загрузите документы заново. То же после смены `LAWYER_CHUNK_SIZE` / `LAWYER_CHUNK_OVERLAP`.
3. Создадутся папки `secretary/uploaded`, `lawyer/uploaded`, `chroma_data`.

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

## Развёртывание на сервере (Linux / VPS)

Ниже — типичный цикл: установка, запуск, обновление кода, остановка процесса.

### Подготовка

```bash
cd ~/AI_DID                    # каталог проекта на сервере
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Создайте `.env` (по образцу `.env.example`): ключи API, `N8N_ECONOMIST_WEBHOOK_URL`, настройки Whisper и OCR.

Дополнительно на сервере:

- `sudo apt install -y ffmpeg` — для модуля Секретарь;
- `pip install python-docx` — если DOCX в Юристе не читается (должен ставиться из `requirements.txt`, но проверьте после деплоя).

### Обновление кода с GitHub

```bash
cd ~/AI_DID
git pull
source venv/bin/activate
pip install -r requirements.txt   # при изменении зависимостей
```

После обновления перезапустите uvicorn (см. ниже).

### Запуск приложения

Перейдите в каталог проекта и активируйте venv:

```bash
cd ~/AI_DID
source venv/bin/activate
```

**Проверка в текущей сессии SSH** (процесс завершится при закрытии терминала):

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Для разработки на своём ПК можно добавить `--reload`; на сервере для постоянной работы **лучше без `--reload`**.

**Фоновый запуск** — приложение продолжит работать после закрытия SSH:

```bash
nohup uvicorn main:app --host 0.0.0.0 --port 8000 --reload > uvicorn.log 2>&1 &
```

- `nohup` и `&` — процесс не привязан к терминалу;
- `> uvicorn.log 2>&1` — логи в файл `uvicorn.log` в каталоге проекта;
- `--reload` — перезапуск при изменении файлов (удобно при правках на сервере; для «чистого» продакшена можно убрать).

Проверка, что процесс поднялся:

```bash
tail -f uvicorn.log
```

В логе должно появиться: `Uvicorn running on http://0.0.0.0:8000`.

### Доступ в браузере

- Адрес **`http://0.0.0.0:8000`** в браузере не открывайте — это служебный bind «все интерфейсы».
- Открывайте **`http://ПУБЛИЧНЫЙ_IP_СЕРВЕРА:8000`** (IP из панели хостинга).
- Если страница не открывается — откройте порт **8000** в firewall / security group VPS.

### Остановка и перезапуск

**Найти процесс по порту 8000:**

```bash
lsof -i :8000
```

В колонке `PID` — номер процесса (например, `12345`).

**Завершить процесс:**

```bash
kill 12345
```

Подставьте свой PID. Если процесс не завершился: `kill -9 12345`.

После остановки снова запустите uvicorn (интерактивно или через `nohup`).

### Зависимости и типичные проблемы

| Ситуация | Что сделать |
|----------|-------------|
| Первый запуск Whisper | Скачается модель (~150 МБ для `base`). В `.env`: `WHISPER_MODEL_SIZE=base`, `WHISPER_BEAM_SIZE=1`; опционально `WHISPER_PRELOAD=true`. |
| Эмбеддинги офлайн | Модель в `models/` или `EMBEDDING_PROVIDER=openai` / `gigachat`. |
| Смена провайдера эмбеддингов | Очистить индекс Юриста и загрузить документы заново (векторы разных моделей несовместимы). |
| RapidOCR | При первом OCR скачаются ONNX-модели (~десятки МБ). |
| Загрузка большого TXT/DOCX, ошибка `max_tokens_per_request` | Обновите код (`git pull`) — эмбеддинги OpenAI идут пакетами. Либо `EMBEDDING_PROVIDER=local`. |
| TXT с «пїЅпїЅ…» вместо русского текста | Файл в CP1251, а читался как UTF-8. После `git pull` кодировка подбирается автоматически; надёжнее сохранить TXT в **UTF-8**. |
| После загрузки 2-го документа не ищет в первом | Обновите код: поиск идёт **по каждому файлу отдельно**, в ответ попадают фрагменты с обоих. |
| OCR: `Killed` в логе | Мало RAM. В `.env`: `LAWYER_OCR_SCALE=1.0`, `LAWYER_OCR_MAX_SIDE=1200`, swap 2 ГБ или загрузка DOCX вместо скан-PDF. |
| OCR: `libGL.so.1` | `pip uninstall -y opencv-python && pip install opencv-python-headless`, см. `scripts/fix_opencv_server.sh`. |

Внешние программы для модуля Юрист **не используются** (Tesseract, LibreOffice, Word, Poppler) — только пакеты из `requirements.txt`.

## Конфигурация

| Переменная | Описание |
|------------|----------|
| `LLM_PROVIDER` | `gigachat` или `deepseek` |
| `EMBEDDING_PROVIDER` | `local`, `openai` или `gigachat` |
| `GIGACHAT_EMBEDDING_MODEL` | `Embeddings` (по умолчанию) или `EmbeddingsGigaR` |
| `GIGACHAT_MAX_EMBED_CHARS` | лимит символов на один embed-запрос GigaChat (~514 токенов; по умолчанию 950) |
| `EMBED_BATCH_SIZE` | размер пакета эмбеддингов (по умолчанию 16; для больших DOCX/TXT через OpenAI не увеличивайте сильно) |
| `HF_ENDPOINT` | зеркало HuggingFace, напр. `https://hf-mirror.com` |
| `EMBEDDING_LOCAL_FILES_ONLY` | `1` — не обращаться к huggingface.co |
| `LOCAL_EMBEDDING_MODEL` | путь `models/paraphrase-multilingual-MiniLM-L12-v2` |
| `WHISPER_MODEL_SIZE` | `tiny`, `base` (по умолчанию), `small`, `medium`, `large-v3` |
| `WHISPER_BEAM_SIZE` | `1` — быстро на CPU; `5` — точнее, медленнее |
| `WHISPER_CPU_THREADS` | `0` = все ядра; или число потоков |
| `WHISPER_PRELOAD` | `true` — загрузить модель при старте uvicorn |
| `MAX_AUDIO_SIZE` | лимит аудио |
| `MAX_DOCUMENT_SIZE` | лимит документов |
| `ECONOMIST_FACT_SHEET_URL` | ссылка на таблицу факта (`/edit`) |
| `N8N_ECONOMIST_WEBHOOK_URL` | Production URL webhook n8n для чата |
| `N8N_ECONOMIST_TIMEOUT` | таймаут ответа n8n, сек (по умолчанию 120) |
| `LAWYER_CITATION_LLM_REPAIR` | `true` — восстанавливать русский текст в блоке «Источники» через LLM при искажениях PDF/OCR |
| `LAWYER_RETRIEVE_K` / `LAWYER_RETRIEVE_K_MAX` | сколько чанков просматривать при поиске (80 / 180) |
| `LAWYER_CONTEXT_K` | фрагментов в контексте LLM (по умолчанию 8) |
| `LAWYER_CHUNK_SIZE` / `LAWYER_CHUNK_OVERLAP` | размер чанка и перекрытие (1200 / 200); после смены — переиндексация |
| `LAWYER_SEMANTIC_WEIGHT` | вес векторного score в ранжировании (0.45) |
| `LAWYER_SEMANTIC_MIN_SCORE` | порог «чисто семантического» попадания (0.42) |
| `LAWYER_SEMANTIC_TOP_K` | сколько лучших semantic-чанков всегда держать в пуле (6) |
| `LAWYER_BALANCE_FILES` | `false` — не подмешивать слабые фрагменты с других файлов |
| `LAWYER_LLM_QUERY_REWRITE` | `true` — LLM перефразирует вопрос только для embed_query |
| `LAWYER_CONTEXT_MERGE_NEIGHBORS` | склеить ±N соседних чанков перед отправкой в LLM (1) |
| `LAWYER_MIN_COMBINED_SCORE` | порог отсечения слабых фрагментов (по умолчанию `0.12`) |
| `APP_TIMEZONE` | часовой пояс истории действий (по умолчанию `Europe/Moscow`) |

## Структура

```
├── economist/     # чат через n8n, ссылка на таблицу факта
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
