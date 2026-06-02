# Публикация на GitHub

## Перед первым push

1. **Не коммитьте `.env`** — в нём ключи API. В репозитории только `.env.example`.
2. Скопируйте настройки локально: `cp .env.example .env` и заполните секреты.
3. Убедитесь, что в индекс не попали `venv/`, `chroma_data/`, `models/`, `*/uploaded/*` (кроме `.gitkeep`).
4. При необходимости положите `logo.png` и `favicon.png` в `static/img/` или добавьте их в `.gitignore`, если не хотите публиковать.

## Инициализация репозитория

```bash
cd путь/к/ДИД_ассистент
git init
git add .
git status   # проверьте: нет .env, venv, chroma_data
git commit -m "Initial commit: ИИ-помощник ФГУП ДИД"
```

## Создание репозитория на GitHub

1. GitHub → **New repository** (без README, если уже есть локальный коммит).
2. Привязка и push:

```bash
git branch -M main
git remote add origin https://github.com/ВАШ_АККАУНТ/ВАШ_РЕПО.git
git push -u origin main
```

## Если ключи случайно попали в Git

Немедленно **отзовите/перевыпустите** ключи в кабинете DeepSeek/OpenAI/GigaChat и удалите секреты из истории (`git filter-repo` или GitHub Secret scanning).

## Клонирование на сервере

```bash
git clone https://github.com/ВАШ_АККАУНТ/ВАШ_РЕПО.git
cd ВАШ_РЕПО
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактировать .env
uvicorn main:app --host 0.0.0.0 --port 8000
```

См. также раздел «Развёртывание на сервере» в [README.md](../README.md).
