"""Genera los PNG de la app de escritorio a partir de los SVG de `static/pwa/` (una vez; los PNG se versionan).

Uso: `python docs/app-escritorio/iconos.py`. Necesita Playwright con Edge o Chromium (ya está en requirements-dev).
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

PWA = Path(__file__).resolve().parents[2] / "static" / "pwa"
# (svg de origen, png de salida, lado en px)
SALIDAS = (
    ("icono.svg", "icono-192.png", 192),
    ("icono.svg", "icono-512.png", 512),
    ("icono-maskable.svg", "icono-maskable-512.png", 512),
)


def _lanzar(pw):
    for opciones in ({"channel": "msedge"}, {}):
        try:
            return pw.chromium.launch(headless=True, **opciones)
        except Exception:
            continue
    raise SystemExit("no hay Edge ni Chromium para Playwright")


def main():
    with sync_playwright() as pw:
        navegador = _lanzar(pw)
        for origen, salida, lado in SALIDAS:
            pagina = navegador.new_page(viewport={"width": lado, "height": lado})
            svg = (PWA / origen).read_text(encoding="utf-8").replace("<svg ", f'<svg width="{lado}" height="{lado}" ', 1)
            pagina.set_content(f'<html><body style="margin:0;background:transparent">{svg}</body></html>')
            pagina.locator("svg").screenshot(path=str(PWA / salida), omit_background=True)
            pagina.close()
            print(f"{salida}: {lado}x{lado}")
        navegador.close()


if __name__ == "__main__":
    main()
