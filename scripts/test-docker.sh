#!/usr/bin/env bash
# Запуск тестов в docker-контуре (аналог CI, но против локальной БД ticketbot_test).
#
# Использует образ ticketbot-dev (основные + dev зависимости), монтирует текущий
# код и подключается к PostgreSQL по Docker DNS в сети контейнера ticketbot-db.
#
# Собрать образ (один раз, или после изменения deps):
#   docker build -t ticketbot-dev -f - . <<'EOF'
#   FROM ticketbot-telegram
#   WORKDIR /app
#   ENV PYTHONPATH=/app
#   COPY pyproject.toml .
#   COPY . .
#   RUN pip install --no-cache-dir -e '.[dev]'
#   EOF
#
# Usage:
#   scripts/test-docker.sh                    # весь набор
#   scripts/test-docker.sh tests/test_identity.py -v
set -e
cd "$(dirname "$0")/.."

IMAGE="${TICKETBOT_TEST_IMAGE:-ticketbot-dev}"
# Сеть определяется по контейнеру БД; можно переопределить при нестандартной конфигурации.
DB_CONTAINER="${TICKETBOT_TEST_DB_CONTAINER:-ticketbot-db}"
if [ -n "${TICKETBOT_TEST_NETWORK:-}" ]; then
  NETWORK="$TICKETBOT_TEST_NETWORK"
else
  NETWORK="$(docker inspect --format '{{range $name, $net := .NetworkSettings.Networks}}{{$name}}{{end}}' "$DB_CONTAINER")"
fi
if [ -z "$NETWORK" ]; then
  echo "Не найдена Docker-сеть контейнера $DB_CONTAINER." >&2
  exit 1
fi

# Пароль БД берём из DB_PASSWORD_VK (как на деплое), фолбэк postgres для локальной разработки.
# В общей Docker-сети БД доступна по имени контейнера, а не через localhost хоста.
DB_HOST="${TICKETBOT_TEST_DB_HOST:-$DB_CONTAINER}"
DB_URL="postgresql+asyncpg://postgres:${DB_PASSWORD_VK:-postgres}@${DB_HOST}:5432/ticketbot_test"

docker run --rm --network "$NETWORK" --entrypoint pytest \
  -v "$PWD:/app" -w /app \
  -e PYTHONPATH=/app \
  -e DATABASE_URL="$DB_URL" \
  -e TEST_DATABASE_URL="$DB_URL" \
  "$IMAGE" "$@" --timeout=60
