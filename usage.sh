#!/usr/bin/env bash
# usage.sh — lanza la tarjeta de uso de Claude
aqui="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$aqui/.venv/bin/python" "$aqui/uso_card.py" "$@"
