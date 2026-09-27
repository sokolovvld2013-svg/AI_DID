#!/usr/bin/env bash
# Установка / обновление ИИ-помощника на Linux-сервере.
# Запуск:  bash deploy/install.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sokolovvld2013-svg/AI_DID.git}"
APP_DIR="${APP_DIR:-/root/AI_DID}"
SERVICE_NAME="${SERVICE_NAME:-aid}"
PORT="${PORT:-8000}"
BRANCH="${BRANCH:-main}"

log() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

log "1/8 Проверка ресурсов"
free -m | head -2
df -h / | tail -1

log "2/8 Системные пакеты (git, python3-venv, ffmpeg)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip ffmpeg curl

log "3/8 Код приложения ($REPO_URL, ветка $BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" pull --ff-only --quiet origin "$BRANCH"
    echo "обновлено: $(git -C "$APP_DIR" rev-parse --short HEAD)"
else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    echo "склонировано в $APP_DIR"
fi

log "4/8 Настройки (.env)"
if [ -f "$APP_DIR/.env" ]; then
    echo ".env уже есть — не перезаписываю"
elif [ -f "$APP_DIR/.env.local" ]; then
    cp "$APP_DIR/.env.local" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "создан .env из .env.local"
else
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "ВНИМАНИЕ: создан .env из .env.example — заполните ключи и запустите скрипт заново"
fi

log "5/8 Виртуальное окружение и зависимости"
cd "$APP_DIR"
if [ ! -x venv/bin/python ]; then
    python3 -m venv venv
fi
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
./venv/bin/python -c "import fastapi, uvicorn; print('fastapi', fastapi.__version__)"

log "6/8 Рабочие каталоги"
mkdir -p logs chroma_data
for d in economist/uploaded secretary/uploaded lawyer/uploaded; do
    mkdir -p "$d"
done

log "7/8 Служба systemd ($SERVICE_NAME.service)"
cat > /etc/systemd/system/$SERVICE_NAME.service <<UNIT
[Unit]
Description=AI_DID — ИИ-помощник (Экономист, Юрист, Закупка, Торги, Секретарь)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGINT
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
# .env читает само приложение (python-dotenv) из рабочего каталога

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

log "8/8 Проверка"
sleep 6
systemctl is-active "$SERVICE_NAME" || true
curl -fsS -o /dev/null -w "проверка /economist: HTTP %{http_code}\n" "http://127.0.0.1:$PORT/economist" || true
curl -fsS -o /dev/null -w "проверка /api/settings: HTTP %{http_code}\n" "http://127.0.0.1:$PORT/api/settings" || true

cat <<INFO

Готово. Служба: $SERVICE_NAME
  Статус:   systemctl status $SERVICE_NAME
  Логи:     journalctl -u $SERVICE_NAME -f
  Перезапуск после правок: systemctl restart $SERVICE_NAME
  Приложение: http://<IP-сервера>:$PORT
  Не забудьте открыть порт $PORT в firewall и в панели хостинга.
INFO
