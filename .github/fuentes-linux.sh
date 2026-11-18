#!/usr/bin/env bash
# Fuentes del runner de Linux como en Windows (13/11): la interfaz usa la fuente del sistema (font-sans de Tailwind),
# que en Windows es Segoe UI y en Ubuntu DejaVu Sans, más ancha: los tests de layout (títulos que no se pisan a 360 px)
# fallaban solo en Linux. Selawik es la fuente libre de Microsoft con las mismas métricas que Segoe UI.
set -euo pipefail
destino="$HOME/.local/share/fonts/selawik"
mkdir -p "$destino" "$HOME/.config/fontconfig"
curl -fsSL https://github.com/microsoft/Selawik/releases/download/1.01/Selawik_Release.zip -o /tmp/selawik.zip
unzip -o -q -j /tmp/selawik.zip '*.ttf' -d "$destino"
cat > "$HOME/.config/fontconfig/fonts.conf" <<'XML'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <alias binding="strong"><family>sans-serif</family><prefer><family>Selawik</family></prefer></alias>
  <alias binding="strong"><family>system-ui</family><prefer><family>Selawik</family></prefer></alias>
  <alias binding="strong"><family>Segoe UI</family><prefer><family>Selawik</family></prefer></alias>
</fontconfig>
XML
fc-cache -f > /dev/null
fc-match sans-serif
fc-match system-ui
