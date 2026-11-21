"""Chequea (sin modificar nada) que los «Comandos permitidos» de una misión estén en la allowlist de AGY.
Uso: python -X utf8 allowlist_check.py <mision.md> <settings.json>
Imprime un comando faltante por línea y sale con 1 si falta alguno; 0 si están todos o si no hay qué chequear.
AGY compara literal, carácter por carácter, como «command(<texto>)»."""
import json, re, sys
from pathlib import Path

mission, settings = Path(sys.argv[1]), Path(sys.argv[2])
text = mission.read_text(encoding="utf-8")
m = re.search(r"^Comandos permitidos[^:\n]*:(.*?)(?=^\S[^:\n]*:\s|^## |\Z)", text, re.S | re.M)
if not m or not settings.exists():
    sys.exit(0)
cmds = []
for line in m.group(1).splitlines():  # «- `cmd`» o «- cmd» (sin comillas invertidas, como escriben algunas misiones)
    if not line.startswith("- "):
        continue
    ticks = re.findall(r"`([^`]+)`", line)
    cmds += [c.strip() for c in ticks] if ticks else [line[2:].strip()]
cmds = [c for c in cmds if c]
allow = set(json.loads(settings.read_text(encoding="utf-8")).get("permissions", {}).get("allow", []))
missing = [c for c in cmds if f"command({c})" not in allow]
for c in missing:
    print(c)
sys.exit(1 if missing else 0)
