"""Проверки, что приложение собирается и отвечает.

Тесты идут с AUTH_ENABLED=false, поэтому страница входа отдаёт редирект,
а не форму: форму проверяет test_auth.py, который включает авторизацию.
"""

from __future__ import annotations


def test_app_builds(app) -> None:
    assert app.title
    assert app.routes


def test_openapi_generates(app) -> None:
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    assert schema["paths"]


async def test_root_redirects_to_economist(client) -> None:
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/economist"


async def test_economist_page_renders(client) -> None:
    response = await client.get("/economist")
    assert response.status_code == 200


async def test_login_redirects_when_auth_disabled(client) -> None:
    response = await client.get("/login", follow_redirects=False)
    assert response.status_code == 302


async def test_unknown_path_returns_404(client) -> None:
    response = await client.get("/__definitely_missing__")
    assert response.status_code == 404


async def test_static_css_is_served(client) -> None:
    response = await client.get("/static/css/style.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")