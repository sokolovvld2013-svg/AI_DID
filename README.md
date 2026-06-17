# ИИ-помощник Компании

Веб-приложение для сотрудников с тремя модулями: **Экономист**, **Секретарь**, **Юрист**.  
Один сайт, общая база нормативных документов (Юрист), личная история запросов у каждого пользователя (по cookie в браузере).

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

## Модули

### Экономист

Помощник по бюджету и фактическим расходам.

**Что умеет**

- Отвечать на вопросы в чате: подбор статьи по описанию расхода, лимиты ПД по статье или объекту, факт по статье.
- Показывать ответ текстом или в виде таблицы (если n8n вернул структурированные данные).
- Открывать **таблицу факта** в Google Sheets — кнопка «Открыть таблицу» на странице модуля.

**Как устроено**

1. Пользователь пишет вопрос в чат на сайте.
2. Сервер отправляет запрос в **n8n** (`N8N_ECONOMIST_WEBHOOK_URL`).
3. В n8n выполняется ваша логика: LLM, Google Sheets, 1С и т.д.
4. Ответ возвращается в чат; история сохраняется **только для этого браузера**.

**Настройка**

- `ECONOMIST_FACT_SHEET_URL` — ссылка на Google-таблицу факта (режим редактирования).
- `N8N_ECONOMIST_WEBHOOK_URL` — Production URL webhook в n8n.

Подробная схема workflow: [docs/N8N_ECONOMIST.md](docs/N8N_ECONOMIST.md).

---

### Секретарь

Протоколирование совещаний из аудиозаписи.

**Что умеет**

- Принимать аудио: `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac` (до 100 МБ по умолчанию).
- Распознавать речь (**Whisper**, русский язык).
- Формировать **протокол совещания** через LLM: тема, участники, решения, поручения.
- Хранить историю обработанных файлов **для каждого пользователя**; открывать сохранённые протоколы из списка.

**Как устроено**

1. Загрузка файла на сервер.
2. Транскрибация (локально, модель Whisper).
3. Структурирование текста LLM (DeepSeek или GigaChat).
4. Протокол отображается на странице и попадает в личную историю.

**На сервере нужен** `ffmpeg` (`sudo apt install -y ffmpeg` на Linux).

---

### Юрист

Поиск ответов в загруженных внутренних документах (положения, регламенты, приказы).

**Что умеет**

- Загружать **DOCX**, **TXT**, **PDF** (до 50 МБ, до 200 страниц).
- Индексировать документы в **общую** базу (ChromaDB) — все сотрудники видят один набор файлов и задают вопросы по нему.
- Отвечать на вопросы с **цитатами** и указанием файла и страницы.
- Удалять отдельные файлы или очищать всю базу.

**Как устроено**

1. Документ разбивается на фрагменты (чанки), строятся векторные эмбеддинги.
2. На вопрос — гибридный поиск (ключевые слова + семантика).
3. Релевантные фрагменты передаются в LLM; ответ со ссылками `[1]`, `[2]`…
4. Блок «Источники» показывает выдержки из документов; при искажениях OCR текст может восстанавливаться через LLM.

**Форматы PDF**

- С текстовым слоем: `pymupdf`, `pypdfium2`, `pdfplumber`, `pypdf`.
- Сканы (картинки): **RapidOCR** (только Python-пакеты, без Tesseract/LibreOffice/Word).

Для сканов предпочтительнее загружать **DOCX** — быстрее и точнее, чем OCR PDF.

**История вопросов** — личная (по браузеру). **База документов** — общая для Компании.

---

## Установка (локально)

### Windows

```bash
cd C:\projects\ai-assistant
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

### Linux / macOS

```bash
cd ~/ai-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Заполните `.env`: API-ключи LLM, webhook n8n, при необходимости — ссылку на таблицу факта.

**Эмбеддинги на VPS без GPU** — укажите в `.env`:

```env
EMBEDDING_PROVIDER=gigachat
GIGACHAT_CREDENTIALS=...
```

Так не потребуется PyTorch и пакеты `nvidia_*` (экономия >2 ГБ на диске).

**Локальные эмбеддинги на ПК** (`EMBEDDING_PROVIDER=local`): установите PyTorch CPU и sentence-transformers отдельно, затем при необходимости скачайте модель:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "sentence-transformers>=2.3.0,<3.0.0"
python scripts/download_embedding_model.py
```

В `.env`: `EMBEDDING_LOCAL_FILES_ONLY=1`, путь к модели в `LOCAL_EMBEDDING_MODEL`.

### Первый запуск

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Откройте: http://localhost:8000

При первом запуске:

1. **Whisper** — скачает модель `base` (~150 МБ).
2. **Эмбеддинги** — при `gigachat`/`openai` внешний API; при `local` — модель из `models/` или HuggingFace (`HF_ENDPOINT` для зеркала).
3. Создадутся каталоги `secretary/uploaded`, `lawyer/uploaded`, `chroma_data`.

После смены `EMBEDDING_PROVIDER` или параметров чанков (`LAWYER_CHUNK_SIZE`, `LAWYER_CHUNK_OVERLAP`) очистите индекс Юриста и загрузите документы заново.

---

## Развёртывание на сервере (VPS)

### Подготовка

```bash
cd /opt/ai-assistant
git clone https://github.com/USER/REPO.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env
sudo apt install -y ffmpeg
```

Рекомендуется `EMBEDDING_PROVIDER=gigachat` (или `openai`) — не устанавливайте локальные эмбеддинги на маленьком VPS.

### Обновление

```bash
cd /opt/ai-assistant
git pull
source venv/bin/activate
pip install -r requirements.txt
# перезапустите uvicorn
```

### Запуск

Перейдите в каталог проекта и активируйте venv (иначе `main:app` и `logs/` не найдутся):

```bash
cd ~/AI_DID
source venv/bin/activate
mkdir -p logs
```

Интерактивно (для проверки):

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

В фоне:

```bash
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > logs/uvicorn.log 2>&1 &
tail -f logs/uvicorn.log
```

Если каталог проекта другой — подставьте свой путь вместо `~/AI_DID`.

На продакшене лучше **без** `--reload`.

### Доступ

- В браузере: `http://IP_СЕРВЕРА:8000` (не используйте `0.0.0.0` как адрес).
- Откройте порт **8000** в firewall / security group.

### Остановка

```bash
lsof -i :8000
kill <PID>
```

---

## Конфигурация (.env)

| Переменная | Назначение |
|------------|------------|
| `LLM_PROVIDER` | `deepseek` или `gigachat` |
| `DEEPSEEK_API_KEY` / `GIGACHAT_CREDENTIALS` | Ключи API |
| `EMBEDDING_PROVIDER` | `gigachat`, `openai` или `local` |
| `GIGACHAT_MAX_EMBED_CHARS` | Лимит символов на запрос эмбеддинга GigaChat (по умолчанию 950) |
| `ECONOMIST_FACT_SHEET_URL` | Google-таблица факта |
| `N8N_ECONOMIST_WEBHOOK_URL` | Webhook n8n для чата Экономиста |
| `N8N_ECONOMIST_TIMEOUT` | Таймаут ответа n8n, сек (120) |
| `WHISPER_MODEL_SIZE` | `tiny`, `base`, `small`, … |
| `WHISPER_DEVICE` | `cpu` или `cuda` (при наличии GPU) |
| `MAX_AUDIO_SIZE` / `MAX_DOCUMENT_SIZE` | Лимиты загрузки, байты |
| `LAWYER_CHUNK_SIZE` / `LAWYER_CHUNK_OVERLAP` | Чанки для RAG (1200 / 200) |
| `LAWYER_OCR_*` | Параметры OCR для PDF-сканов |
| `APP_TIMEZONE` | Часовой пояс истории (`Europe/Moscow`) |
| `HISTORY_SIZE` | Сколько записей хранить в истории на пользователя (5) |

Полный шаблон: [.env.example](.env.example).

---

## Структура проекта

```
ai-assistant/
├── economist/          # чат через n8n
├── secretary/          # аудио → Whisper → протокол
├── lawyer/             # документы, RAG, цитаты
├── core/               # LLM, эмбеддинги, история, сессии
├── static/             # CSS, JS, img (логотип Компании)
├── templates/          # HTML-страницы
├── docs/               # инструкции (n8n, GitHub)
├── scripts/            # утилиты (модель эмбеддингов, диагностика PDF)
├── main.py
├── config.py
└── requirements.txt
```

---

## Типичные проблемы

| Ситуация | Решение |
|----------|---------|
| pip качает `nvidia_*`, нет места на диске | Только `requirements.txt` + `EMBEDDING_PROVIDER=gigachat`, без локального PyTorch |
| Whisper долго на CPU | `WHISPER_MODEL_SIZE=base`, `WHISPER_BEAM_SIZE=1` |
| OCR PDF: `Killed` | Мало RAM: `LAWYER_OCR_SCALE=1.0`, `LAWYER_OCR_MAX_SIDE=1200`, загружайте DOCX |
| OCR: `libGL.so.1` | `pip uninstall -y opencv-python && pip install opencv-python-headless` |
| Смена эмбеддингов | Очистить индекс Юриста, загрузить документы заново |
| Кракозябры в TXT | Сохранить файл в UTF-8 |
| Экономист молчит | Проверить `N8N_ECONOMIST_WEBHOOK_URL`, workflow Active в n8n |

---

## GitHub

Не коммитьте `.env` с ключами. Шаблон: `.env.example`.  
Инструкция по репозиторию: [docs/GITHUB.md](docs/GITHUB.md).

---

## Примечания

- **История** — в памяти сервера, отдельно для каждого браузера (cookie `did_sid`); сбрасывается при перезапуске uvicorn.
- **Документы Юриста** — общие для всех пользователей (ChromaDB в `chroma_data/`).
- **Секретарь и Юрист** требуют рабочий LLM API; **Экономист** в чате зависит от n8n, не от LLM на сервере приложения.
- Логотип и фавикон: `static/img/logo.png`, `static/img/favicon.png` (или пути в `LOGO_SOURCE` / `FAVICON_SOURCE`).
