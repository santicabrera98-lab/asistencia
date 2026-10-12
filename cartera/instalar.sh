#!/bin/bash
# Instala dependencias y programa el reporte con launchd (lunes a viernes 9:30 y 17:15, hora del Mac).
# Uso:  cd cartera && ./instalar.sh
set -e
CARPETA="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/com.cartera.reporte.plist"

echo "→ Instalando dependencias (yfinance, requests, pandas)..."
"$PYTHON" -m pip install --user -q -r "$CARPETA/requirements.txt" 2>/dev/null \
  || "$PYTHON" -m pip install -q --break-system-packages -r "$CARPETA/requirements.txt"

mkdir -p "$CARPETA/logs" "$HOME/Library/LaunchAgents"

echo "→ Generando $PLIST"
sed -e "s|__PYTHON__|$PYTHON|g" -e "s|__CARPETA__|$CARPETA|g" \
    "$CARPETA/com.cartera.reporte.plist.template" > "$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "→ Programado: lunes a viernes 9:30 y 17:15 (hora local del Mac). Logs en $CARPETA/logs/"
echo "→ Corriendo una vez ahora para probar..."
"$PYTHON" "$CARPETA/reporte.py" | head -12
echo "..."
echo "Listo. Para correrlo a mano:  python3 $CARPETA/reporte.py"
