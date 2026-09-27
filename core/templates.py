"""Общий объект Jinja2Templates: в шаблонах доступна company_name()."""
from fastapi.templating import Jinja2Templates

from config import BASE_DIR
from core.settings import get_company_name

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["company_name"] = get_company_name
