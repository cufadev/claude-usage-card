#!/usr/bin/env bash
# Genera Uso-x86_64.AppImage (PySide6 + PyInstaller + appimagetool). Uso: ./build-appimage.sh
# Variables opcionales:
#   APPIMAGETOOL=ruta   appimagetool ya descargado (si no, se baja a .cache/ la primera vez)
#   SALIDA=ruta         dónde dejar el AppImage (por defecto ./Uso-x86_64.AppImage)
# Se compila en esta máquina (no en un contenedor): el AppImage pide una glibc igual de nueva que la de acá.
set -euo pipefail
cd "$(dirname "$0")"
nombre=uso-claude script=uso_card.py titulo=Uso
SALIDA=${SALIDA:-$PWD/$titulo-x86_64.AppImage}
TOOL=${APPIMAGETOOL:-$PWD/.cache/appimagetool-x86_64.AppImage}
if [ ! -x "$TOOL" ]; then
    mkdir -p "$(dirname "$TOOL")"
    curl -fL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
B=$(mktemp -d); trap 'rm -rf "$B"' EXIT
python3 -m venv "$B/venv"
"$B/venv/bin/pip" install -q -r requirements.txt pyinstaller
# --add-binary libssl/libcrypto: la app consulta la API por https
"$B/venv/bin/pyinstaller" --noconfirm --clean --onedir --name "$nombre" --distpath "$B/out" --workpath "$B/work" \
    --specpath "$B/work" --exclude-module tkinter \
    --add-data "$PWD/bebas-neue.woff2:." \
    --add-binary "/lib/x86_64-linux-gnu/libssl.so.3:." --add-binary "/lib/x86_64-linux-gnu/libcrypto.so.3:." \
    "$script" >/dev/null
# ícono: la propia tarjeta dibujada, a 256 px
QT_QPA_PLATFORM=offscreen "$B/venv/bin/python" "$script" --captura "$B/grande.png" >/dev/null 2>&1
"$B/venv/bin/python" -c "
from PySide6.QtGui import QImage; from PySide6.QtCore import Qt
QImage('$B/grande.png').scaled(256,256,Qt.KeepAspectRatio,Qt.SmoothTransformation).save('$B/icono.png')"
A=$B/AppDir; mkdir -p "$A/usr/lib" "$A/usr/share/applications"
cp -r "$B/out/$nombre" "$A/usr/lib/$nombre"
cat > "$A/$nombre.desktop" <<EOD
[Desktop Entry]
Type=Application
Name=$titulo
Exec=$nombre
Icon=$nombre
Terminal=false
Categories=Utility;
StartupWMClass=$nombre
EOD
cp "$A/$nombre.desktop" "$A/usr/share/applications/"
cp "$B/icono.png" "$A/$nombre.png"; cp "$B/icono.png" "$A/.DirIcon"
printf '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/lib/%s/%s" "$@"\n' "$nombre" "$nombre" > "$A/AppRun"
chmod +x "$A/AppRun"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$A" "$SALIDA" >/dev/null 2>&1
ls -lh "$SALIDA"
