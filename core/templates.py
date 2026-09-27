"""Общий объект Jinja2Templates: в шаблонах доступны company_name() и logo_url()."""
from fastapi.templating import Jinja2Templates

from config import BASE_DIR
from core.settings import get_company_name, logo_url

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["company_name"] = get_company_name
templates.env.globals["logo_url"] = logo_url
