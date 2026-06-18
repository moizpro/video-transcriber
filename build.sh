#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  build.sh — Genera el .app de Video Transcriber con PyInstaller
#  Uso: bash build.sh
# ─────────────────────────────────────────────────────────────

set -e  # Detener si cualquier comando falla

APP_NAME="Video Transcriber"
SCRIPT="transcriber_app.py"
DIST_DIR="dist"

echo "▸ Limpiando builds anteriores…"
rm -rf build dist __pycache__ *.spec

echo "▸ Generando .app con PyInstaller…"
pyinstaller \
  --name "$APP_NAME" \
  --windowed \
  --onedir \
  --noconfirm \
  --icon icon.icns \
  --hidden-import="customtkinter" \
  --hidden-import="faster_whisper" \
  --hidden-import="ctranslate2" \
  --hidden-import="tokenizers" \
  --hidden-import="huggingface_hub" \
  --hidden-import="certifi" \
  --collect-all customtkinter \
  --collect-all faster_whisper \
  --collect-all ctranslate2 \
  "$SCRIPT"

echo ""
echo "✓ Build completado en:  $DIST_DIR/$APP_NAME.app"
echo ""
echo "▸ Moviendo al Escritorio…"
DESKTOP="$HOME/Desktop"
DEST="$DESKTOP/$APP_NAME.app"

# Si ya existe una versión anterior en el Escritorio, la reemplaza
[ -d "$DEST" ] && rm -rf "$DEST"
cp -R "$DIST_DIR/$APP_NAME.app" "$DEST"

echo "✓ App disponible en el Escritorio: $DEST"
echo ""
echo "Nota: el primer lanzamiento descargará el modelo Whisper"
echo "seleccionado (~500 MB para 'medium') y lo guardará en:"
echo "  ~/.cache/huggingface/hub/"
echo "Las ejecuciones siguientes lo usarán desde la caché."
