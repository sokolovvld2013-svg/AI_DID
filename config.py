"""Конфигурация приложения из переменных окружения."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Логотип и фавиконка (положите файлы в static/img/ или укажите LOGO_SOURCE / FAVICON_SOURCE в .env)
STATIC_LOGO = BASE_DIR / "static" / "img" / "logo.png"
STATIC_FAVICON = BASE_DIR / "static" / "img" / "favicon.png"


def _asset_path_from_env(var_name: str, default: Path) -> Path:
    raw = os.getenv(var_name, "").strip()
    if raw:
        return Path(raw).expanduser()
    return default


LOGO_SOURCE = _asset_path_from_env("LOGO_SOURCE", STATIC_LOGO)
FAVICON_SOURCE = _asset_path_from_env("FAVICON_SOURCE", STATIC_FAVICON)

# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").lower()
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat")

# Эмбеддинги
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002")
DEFAULT_EMBEDDING_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODELS_DIR = BASE_DIR / "models"
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "").strip()
HF_HUB_DOWNLOAD_TIMEOUT = os.getenv("HF_HUB_DOWNLOAD_TIMEOUT", "120")
EMBEDDING_LOCAL_FILES_ONLY = os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def configure_huggingface_env() -> None:
    """Зеркало и таймауты HuggingFace (до загрузки sentence-transformers)."""
    if HF_ENDPOINT:
        os.environ["HF_ENDPOINT"] = HF_ENDPOINT.rstrip("/")
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", HF_HUB_DOWNLOAD_TIMEOUT)


configure_huggingface_env()


def resolve_local_embedding_model() -> str:
    """Путь к локальной папке models/… или ID модели на HuggingFace."""
    env = os.getenv("LOCAL_EMBEDDING_MODEL", "").strip()
    candidates: list[Path] = []
    if env:
        p = Path(env)
        candidates.append(p if p.is_absolute() else BASE_DIR / p)
    candidates.append(MODELS_DIR / "paraphrase-multilingual-MiniLM-L12-v2")

    for folder in candidates:
        if folder.is_dir() and any((folder / name).exists() for name in ("config.json", "modules.json")):
            return str(folder.resolve())
    return env or DEFAULT_EMBEDDING_MODEL_ID


LOCAL_EMBEDDING_MODEL = resolve_local_embedding_model()

# Whisper (Секретарь): на CPU VPS — base + beam_size=1 заметно быстрее small + beam 5
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_BEAM_SIZE = max(1, int(os.getenv("WHISPER_BEAM_SIZE", "1")))
WHISPER_CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "0"))  # 0 = все ядра CPU
WHISPER_VAD_FILTER = os.getenv("WHISPER_VAD_FILTER", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
WHISPER_CONDITION_ON_PREVIOUS = os.getenv(
    "WHISPER_CONDITION_ON_PREVIOUS", "false"
).strip().lower() in ("1", "true", "yes")
WHISPER_PRELOAD = os.getenv("WHISPER_PRELOAD", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)
WHISPER_LOG_EVERY_N_SEGMENTS = max(1, int(os.getenv("WHISPER_LOG_EVERY_N_SEGMENTS", "15")))

# Лимиты загрузки (байты)
MAX_EXCEL_SIZE = int(os.getenv("MAX_EXCEL_SIZE", 20 * 1024 * 1024))
MAX_AUDIO_SIZE = int(os.getenv("MAX_AUDIO_SIZE", 100 * 1024 * 1024))
MAX_DOCUMENT_SIZE = int(os.getenv("MAX_DOCUMENT_SIZE", 50 * 1024 * 1024))
MAX_LAWYER_PAGES = int(os.getenv("MAX_LAWYER_PAGES", 200))
# OCR PDF-сканов: меньше scale/max_side — меньше RAM (на VPS: 1.0 и 1200)
LAWYER_OCR_SCALE = float(os.getenv("LAWYER_OCR_SCALE", "1.0"))
LAWYER_OCR_MAX_SIDE = int(os.getenv("LAWYER_OCR_MAX_SIDE", "1200"))
# 0 = все страницы; на слабом VPS можно 6–8
LAWYER_OCR_MAX_PAGES = int(os.getenv("LAWYER_OCR_MAX_PAGES", "0"))
# OCR в отдельном процессе — при OOM не падает весь uvicorn
LAWYER_OCR_SUBPROCESS = os.getenv("LAWYER_OCR_SUBPROCESS", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
LAWYER_OCR_TIMEOUT_SEC = int(os.getenv("LAWYER_OCR_TIMEOUT_SEC", "900"))
# Восстановление читаемого русского в блоке «Источники» (LLM, если OCR/PDF исказил текст)
LAWYER_CITATION_LLM_REPAIR = os.getenv("LAWYER_CITATION_LLM_REPAIR", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
MAX_LAWYER_CITATION_CHARS = int(os.getenv("MAX_LAWYER_CITATION_CHARS", 2500))
MAX_LAWYER_LLM_CONTEXT_CHARS = int(os.getenv("MAX_LAWYER_LLM_CONTEXT_CHARS", 28000))

# ChromaDB
CHROMA_PERSIST_DIR = BASE_DIR / os.getenv("CHROMA_PERSIST_DIR", "chroma_data")

# История
HISTORY_SIZE = int(os.getenv("HISTORY_SIZE", 5))

# Пути модулей
ECONOMIST_UPLOAD_DIR = BASE_DIR / "economist" / "uploaded"
SECRETARY_UPLOAD_DIR = BASE_DIR / "secretary" / "uploaded"
LAWYER_UPLOAD_DIR = BASE_DIR / "lawyer" / "uploaded"

ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
ALLOWED_DOC_EXT = {".pdf", ".docx", ".txt"}
ALLOWED_EXCEL_EXT = {".xlsx", ".xls"}

# Google-таблица фактических затрат (ссылка с правом редактирования)
ECONOMIST_FACT_SHEET_URL = os.getenv("ECONOMIST_FACT_SHEET_URL", "").strip()


def normalize_google_sheet_edit_url(url: str) -> str:
    """Ссылка на лист в режиме редактирования."""
    if not url:
        return ""
    raw = url.strip()
    fragment = ""
    if "#" in raw:
        raw, frag = raw.split("#", 1)
        fragment = f"#{frag}"
    base = raw.split("?")[0].rstrip("/")
    if "/view" in base:
        base = base.replace("/view", "/edit", 1)
    elif "/edit" not in base and "docs.google.com/spreadsheets/d/" in base:
        base = f"{base}/edit"
    return base + fragment


ECONOMIST_FACT_SHEET_EDIT_URL = normalize_google_sheet_edit_url(ECONOMIST_FACT_SHEET_URL)

# n8n — чат Экономиста
N8N_ECONOMIST_WEBHOOK_URL = os.getenv("N8N_ECONOMIST_WEBHOOK_URL", "").strip()
N8N_ECONOMIST_WEBHOOK_METHOD = os.getenv("N8N_ECONOMIST_WEBHOOK_METHOD", "POST").strip().upper()
if N8N_ECONOMIST_WEBHOOK_METHOD not in ("POST", "GET"):
    N8N_ECONOMIST_WEBHOOK_METHOD = "POST"
N8N_ECONOMIST_TIMEOUT = float(os.getenv("N8N_ECONOMIST_TIMEOUT", "120"))
