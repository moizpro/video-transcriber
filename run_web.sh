#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  run_web.sh — Arranca la interfaz web de Video Transcriber
#  Uso: bash run_web.sh
# ─────────────────────────────────────────────────────────────
set -e

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "✗ No existe .env — copia .env.example a .env y rellena los valores."
  exit 1
fi

# Carga las variables de .env en el entorno de este script
set -a
source .env
set +a

echo "▸ Arrancando Video Transcriber en http://localhost:${PORT:-8000}"
echo "  (Ctrl+C para detenerlo)"
echo ""

exec uvicorn web.server:app --host 0.0.0.0 --port "${PORT:-8000}"
