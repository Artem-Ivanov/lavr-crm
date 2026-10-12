#!/bin/sh
set -eu
umask 077

case "${1:-}" in
    '') no_build=false ;;
    --no-build) no_build=true ;;
    -h|--help) printf 'Usage: scripts/start.sh [--no-build]\n'; exit 0 ;;
    *) printf 'Usage: scripts/start.sh [--no-build]\n' >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
    printf 'Usage: scripts/start.sh [--no-build]\n' >&2
    exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
    printf 'Установите и запустите Docker с Compose v2.\n' >&2
    exit 1
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
backend_image=lavr-crm-backend:local
env_file=.env.docker

if [ ! -f "$env_file" ]; then
    if ! docker image inspect "$backend_image" >/dev/null 2>&1; then
        if [ "$no_build" = true ]; then
            printf 'Не найден %s. Сначала выполните docker load или запуск без --no-build.\n' "$backend_image" >&2
            exit 1
        fi
        docker build --tag "$backend_image" backend
    fi
    env_tmp=$(mktemp "$project_dir/.env.docker.tmp.XXXXXX")
    trap 'rm -f "$env_tmp"' EXIT HUP INT TERM
    docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
        --entrypoint python "$backend_image" -m app.init_env > "$env_tmp"
    if ! ln "$env_tmp" "$env_file" 2>/dev/null && [ ! -f "$env_file" ]; then
        printf 'Не удалось создать %s; существующий файл не изменён.\n' "$env_file" >&2
        exit 1
    fi
    rm -f "$env_tmp"
    trap - EXIT HUP INT TERM
    printf 'Создан .env.docker с уникальными паролями. Сохраните его отдельно от образов.\n'
fi
chmod 600 "$env_file"

if [ "$no_build" = true ]; then
    docker compose --env-file "$env_file" up -d --no-build --pull never --wait
else
    docker compose --env-file "$env_file" up -d --build --wait
fi
