"""Mide el contraste WCAG 2.2 de cada par de tokens de static/css/tokens.css (fuente única del rediseño).

Uso: python tools/contraste_tokens.py  -> tabla Markdown; sale con 1 si algún par no
llega a su mínimo. tests/test_rediseno_base.py exige que todos lleguen.
Texto normal: 4.5. Componentes de interfaz (bordes de campos, foco, íconos solos): 3.0.
"""
import pathlib
import re
import sys

TOKENS = pathlib.Path(__file__).resolve().parent.parent / "static" / "css" / "tokens.css"

# (texto o elemento, fondo, mínimo, uso)
PARES = [
    *[(t, f, 4.5, "texto") for t in ("text-primary", "text-secondary", "text-muted")
      for f in ("bg-canvas", "bg-surface", "bg-elevated", "bg-hover", "bg-selected")],
    *[(t, f, 4.5, "estado / enlace") for t in ("accent", "success", "warning", "danger")
      for f in ("bg-canvas", "bg-surface", "bg-elevated", "bg-hover")],
    ("accent", "accent-soft", 4.5, "chip de estado"),
    ("success", "success-soft", 4.5, "chip de estado"),
    ("warning", "warning-soft", 4.5, "chip de estado"),
    ("danger", "danger-soft", 4.5, "chip de estado"),
    *[("text-primary", s, 4.5, "texto en aviso") for s in ("accent-soft", "success-soft", "warning-soft", "danger-soft")],
    *[("text-muted", s, 4.5, "texto en aviso") for s in ("accent-soft", "success-soft", "warning-soft", "danger-soft")],
    ("on-accent", "accent", 4.5, "botón primario"),
    ("on-accent", "accent-hover", 4.5, "botón primario en hover"),
    ("on-accent", "success", 4.5, "botón confirmar"),
    ("on-accent", "danger", 4.5, "botón destructivo lleno"),
    ("text-primary", "bg-field", 4.5, "valor de campo"),
    ("text-muted", "bg-field", 4.5, "placeholder"),
    ("border-control", "bg-surface", 3.0, "borde de campo"),
    ("border-control", "bg-canvas", 3.0, "borde de campo"),
    ("border-control", "bg-elevated", 3.0, "borde de casilla en hoja"),
    ("accent", "bg-surface", 3.0, "anillo de foco"),
    ("accent", "bg-elevated", 3.0, "anillo de foco"),
]


def leer_tokens():
    texto = TOKENS.read_text(encoding="utf-8")
    return {m.group(1): m.group(2).upper() for m in re.finditer(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\b", texto)}


def luminancia(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contraste(a, b):
    la, lb = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def main():
    t = leer_tokens()
    fallas = 0
    print("| Par (frente / fondo) | Valores | Uso | Mínimo | Medido | Resultado |")
    print("|---|---|---|---|---|---|")
    for frente, fondo, minimo, uso in PARES:
        r = contraste(t[frente], t[fondo])
        ok = r >= minimo
        fallas += not ok
        print(f"| `{frente}` / `{fondo}` | {t[frente]} / {t[fondo]} | {uso} | {minimo} | {r:.2f} | {'AA' if ok else 'NO'} |")
    print(f"\n{len(PARES)} pares, {fallas} por debajo del mínimo.")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
