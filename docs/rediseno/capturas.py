"""Capturas de los prototipos a 375 y 1200 px, con control de scroll horizontal.

Uso: python docs/rediseno/capturas.py [filtro]  -> PNG en docs/rediseno/capturas/ y resumen; sale con 1 si alguna
captura tiene scroll horizontal (scrollWidth del documento mayor que el ancho de la ventana).
Abre los HTML como archivos locales: no levanta ningún servidor ni toca el portal.
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

RAIZ = pathlib.Path(__file__).parent
PROTO = RAIZ / "prototipo"
SALIDA = RAIZ / "capturas"

# (nombre, página con parámetros, ancho, alto, página completa)
CAPTURAS = [
    ("radar-1200", "radar.html", 1200, 800, False),
    ("radar-1200-filtros", "radar.html?filtros=1", 1200, 800, False),
    ("radar-1200-procesos", "radar.html?procesos=1", 1200, 800, False),
    ("radar-375-lista", "radar.html?paso=lista", 375, 812, False),
    ("radar-375-controles", "radar.html?paso=lista&extra=1", 375, 812, False),
    ("radar-375-detalle", "radar.html?paso=detalle", 375, 812, True),
    ("radar-375-mail", "radar.html?paso=mail", 375, 812, True),
    ("radar-375-procesos", "radar.html?procesos=1", 375, 812, True),
    ("perfil-1200-datos", "perfil.html#datos", 1200, 800, True),
    ("perfil-1200-busco", "perfil.html#busco", 1200, 800, True),
    ("perfil-1200-sabe", "perfil.html#sabe", 1200, 800, True),
    ("perfil-1200-cv", "perfil.html#cv", 1200, 800, True),
    ("perfil-1200-aprendio", "perfil.html#aprendio", 1200, 800, True),
    ("perfil-1200-automatizacion", "perfil.html#automatizacion", 1200, 800, True),
    ("perfil-1200-salir", "perfil.html?salir=1#datos", 1200, 800, False),
    ("perfil-375-indice", "perfil.html", 375, 812, True),
    ("perfil-375-datos", "perfil.html#datos", 375, 812, True),
    ("perfil-375-busco", "perfil.html#busco", 375, 812, True),
    ("perfil-375-automatizacion", "perfil.html#automatizacion", 375, 812, True),
]


def main():
    filtro = sys.argv[1] if len(sys.argv) > 1 else ""
    SALIDA.mkdir(exist_ok=True)
    fallas = 0
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        for nombre, pagina, ancho, alto, completa in CAPTURAS:
            if filtro not in nombre:
                continue
            ctx = navegador.new_context(viewport={"width": ancho, "height": alto}, device_scale_factor=1,
                                        is_mobile=ancho < 768, has_touch=ancho < 768)
            hoja = ctx.new_page()
            errores = []
            hoja.on("pageerror", lambda e: errores.append(str(e)))
            hoja.goto((PROTO / pagina.split("?")[0].split("#")[0]).as_uri() + pagina[len(pagina.split("?")[0].split("#")[0]):])
            hoja.wait_for_timeout(150)
            ancho_doc = hoja.evaluate("document.documentElement.scrollWidth")
            ventana = hoja.evaluate("window.innerWidth")
            if completa:
                # Página completa: la ventana crece hasta el alto del documento, así las barras fijas quedan abajo
                alto_doc = hoja.evaluate("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)")
                hoja.set_viewport_size({"width": ancho, "height": max(alto, alto_doc)})
                hoja.wait_for_timeout(100)
            hoja.screenshot(path=str(SALIDA / f"{nombre}.png"))
            ok = ancho_doc <= ventana and not errores
            fallas += not ok
            print(f"{'OK ' if ok else 'MAL'} {nombre}: documento {ancho_doc} px, ventana {ventana} px"
                  + (f", errores JS: {errores}" if errores else ""))
            ctx.close()
        navegador.close()
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
