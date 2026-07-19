# ИИ-помощник Компании

Веб-приложение для сотрудников: **Экономист**, **Секретарь**, **Юрист**.  
Один сайт, общая база документов (Юрист), личная история запросов у каждого пользователя (cookie в браузере).

**Каталог проекта на сервере:** `~/AI_DID` (все данные, конфигурация и логи — внутри него).

## Стек

| Слой | Технологии |
|------|------------|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Frontend | HTML, CSS, JavaScript |
| LLM | GigaChat или DeepSeek (`.env`) |
| Поиск по документам (Юрист) | ChromaDB + эмбеддинги GigaChat / OpenAI / локально |
| Речь (Секретарь) | faster-whisper |
| Экономист | n8n (webhook) + Google Таблица |

---

## Каталог `AI_DID`

После установки и первого запуска в `~/AI_DID` находятся:

```
~/AI_DID/
├── .env                    # ключи и настройки (не в git)
├── venv/                   # виртуальное окружение Python
├── logs/                   # uvicorn.log при запуске через nohup
├── chroma_data/            # векторный индекс Юриста (общий для всех)
├── secretary/uploaded/     # загруженные аудиофайлы
├── lawyer/uploaded/        # загруженные документы (копии на диске)
├── models/                 # локальная модель эмбеддингов (если EMBEDDING_PROVIDER=local)
├── economist/              # модуль Экономист
├── secretary/              # модуль Секретарь
├── lawyer/                 # модуль Юрист
├── core/                   # LLM, эмбеддинги, история, сессии
├── static/                 # CSS, JS, img
├── templates/              # HTML
├── docs/                   # инструкции (n8n, GitHub)
├── scripts/                # утилиты
├── main.py
├── config.py
└── requirements.txt
```

Путь можно задать иначе, но далее в инструкциях используется **`~/AI_DID`**.

---

## Модули

### Экономист

Помощник по бюджету и фактическим расходам.

- Чат: подбор статьи, лимиты ПД, факт по статье или объекту.
- Ответ текстом или таблицей (если n8n вернул структурированные данные).
- Кнопка «Открыть таблицу» — Google Sheets с фактом.

**Схема:** вопрос на сайте → webhook **n8n** (`N8N_ECONOMIST_WEBHOOK_URL`) → ваша логика (LLM, Sheets, 1С) → ответ в чат. История — **личная** (по браузеру).

Настройка: `ECONOMIST_FACT_SHEET_URL`, `N8N_ECONOMIST_WEBHOOK_URL`. Подробнее: [docs/N8N_ECONOMIST.md](docs/N8N_ECONOMIST.md).

---

### Секретарь

Протоколы совещаний из аудио.

- Форматы: `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac` (до 100 МБ).
- Распознавание **Whisper** (русский), протокол через LLM.
- История обработанных файлов — **личная**.

На сервере нужен **ffmpeg**: `sudo apt install -y ffmpeg`.

---

### Юрист

Поиск по внутренним документам (положения, приказы, регламенты).

- Загрузка **DOCX**, **TXT** (рекомендуется), **PDF** до 50 МБ.
- На странице указано: *поиск по PDF не эффективен* — для сканов используйте **DOCX** или PDF с текстовым слоем.
- База документов и индекс Chroma — **общие** для всех сотрудников.
- Ответ со ссылками `[1]`, `[2]`…; блок **«Источники»** — только документ и страница (без текста цитаты).

**Как работает RAG**

1. Документ → чанки (~`LAWYER_CHUNK_SIZE` символов) → эмбеддинги в `chroma_data/`.
2. На вопрос — гибридный поиск (ключевые слова + семантика), до **`LAWYER_CONTEXT_K`** фрагментов (по умолчанию 8) в промпт LLM.
3. Эмбеддинг GigaChat обрезается до ~480 символов на фрагмент (`GIGACHAT_MAX_EMBED_CHARS`) — лимит API; **полный текст чанка** хранится в Chroma и передаётся модели.

**PDF:** текстовый слой — через PyMuPDF и др.; сканы — RapidOCR (качество часто низкое). **История вопросов** — личная; **файлы и индекс** — общие.

---

## Установка

### Сервер (Linux, VPS)

```bash
cd ~
git clone https://github.com/USER/REPO.git AI_DID
cd ~/AI_DID
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
sudo apt install -y ffmpeg
mkdir -p logs
```

В `.env` укажите ключи LLM, webhook n8n, при необходимости таблицу факта.

**Эмбеддинги на VPS без GPU** (без PyTorch и `nvidia_*`, экономия >2 ГБ):

```env
EMBEDDING_PROVIDER=gigachat
GIGACHAT_CREDENTIALS=...
GIGACHAT_MAX_EMBED_CHARS=480
```

После смены `EMBEDDING_PROVIDER`, модели эмбеддингов или `LAWYER_CHUNK_*` — **очистите индекс Юриста** и загрузите документы заново.

### Локальная разработка (Windows)

Тот же каталог можно назвать `AI_DID`, например `D:\AI_DID`:

```bat
cd D:\AI_DID
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Локальные эмбеддинги (`EMBEDDING_PROVIDER=local`):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "sentence-transformers>=2.3.0,<3.0.0"
python scripts/download_embedding_model.py
```

Модель сохранится в `~/AI_DID/models/`.

---

## Запуск

Всегда из каталога проекта:

```bash
cd ~/AI_DID
source venv/bin/activate
```

**Проверка (интерактивно):**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Продакшен (фон):**

```bash
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > logs/uvicorn.log 2>&1 &
tail -f logs/uvicorn.log
```

На продакшене **без** `--reload`.

Откройте в браузере: `http://IP_СЕРВЕРА:8000` (не `0.0.0.0`). Порт **8000** — в firewall.

**Остановка:**

```bash
lsof -i :8000
kill <PID>
```

---

## Обновление

```bash
cd ~/AI_DID
git pull
source venv/bin/activate
pip install -r requirements.txt
# перезапустите uvicorn
```

---

## Конфигурация (`.env` в `~/AI_DID`)

| Переменная | Назначение |
|------------|------------|
| `LLM_PROVIDER` | `deepseek` или `gigachat` |
| `DEEPSEEK_API_KEY` / `GIGACHAT_CREDENTIALS` | Ключи API |
| `EMBEDDING_PROVIDER` | `gigachat`, `openai` или `local` |
| `GIGACHAT_MAX_EMBED_CHARS` | Лимит текста на эмбеддинг GigaChat (480; API ~514 токенов) |
| `LAWYER_CHUNK_SIZE` / `LAWYER_CHUNK_OVERLAP` | Размер чанков в индексе (1000 / 150 в типовой настройке) |
| `LAWYER_CONTEXT_K` | Сколько фрагментов максимум в промпт LLM (8) |
| `LAWYER_SEMANTIC_MIN_SCORE` | Порог семантической релевантности (0.42) |
| `ECONOMIST_FACT_SHEET_URL` | Google-таблица факта |
| `N8N_ECONOMIST_WEBHOOK_URL` | Webhook n8n |
| `WHISPER_MODEL_SIZE` | `base`, `small`, … |
| `MAX_AUDIO_SIZE` / `MAX_DOCUMENT_SIZE` | Лимиты загрузки |
| `LAWYER_OCR_*` | OCR PDF-сканов |
| `CHROMA_PERSIST_DIR` | Папка индекса (по умолчанию `chroma_data`) |
| `APP_TIMEZONE` | Часовой пояс истории (`Europe/Moscow`) |
| `HISTORY_SIZE` | Записей истории на пользователя (5) |

Полный шаблон: [.env.example](.env.example).

---

## Типичные проблемы

| Ситуация | Решение |
|----------|---------|
| pip качает `nvidia_*`, нет места | `EMBEDDING_PROVIDER=gigachat`, только `requirements.txt` |
| Ошибка 413 при загрузке TXT/DOCX | `GIGACHAT_MAX_EMBED_CHARS=480`, перезапуск сервера |
| Юрист не находит ответ в PDF | Загрузить **DOCX**; OCR часто искажает текст |
| Кракозябры в TXT в источниках | UTF-8; переиндексировать (удалить файл, загрузить снова) |
| Смена эмбеддингов / чанков | «Очистить базу» на странице Юриста |
| Whisper медленно на CPU | `WHISPER_MODEL_SIZE=base`, `WHISPER_BEAM_SIZE=1` |
| OCR PDF: `Killed` | `LAWYER_OCR_SCALE=1.0`, `LAWYER_OCR_MAX_SIDE=1200`, DOCX |
| OCR: `libGL.so.1` | `pip uninstall -y opencv-python && pip install opencv-python-headless` |
| Экономист молчит | Проверить `N8N_ECONOMIST_WEBHOOK_URL`, workflow Active в n8n |

---

## GitHub

Не коммитьте `~/AI_DID/.env` с ключами. Шаблон: `.env.example`.  
Инструкция: [docs/GITHUB.md](docs/GITHUB.md).

---

## Примечания

- **История** — в памяти процесса uvicorn, по cookie `did_sid`; сбрасывается при перезапуске.
- **Документы и `chroma_data/`** — общие для всех пользователей.
- **Секретарь и Юрист** — LLM на сервере приложения; **Экономист** в чате — через n8n.
- Логотип: `static/img/logo.png`, фавикон: `static/img/favicon.png` (или `LOGO_SOURCE` / `FAVICON_SOURCE`).
