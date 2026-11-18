"""Control visual de cada etapa del rediseño sobre el portal de prueba (1199, datos inventados, sin Radar).

Captura las cinco vistas a 360, 375 y 1200 px, verifica que no haya scroll horizontal ni errores de JS y, con
--comparar, cuenta los píxeles distintos contra otra corrida (p. ej. develop contra la rama de la etapa).

    python docs/rediseno/control_visual.py --salida <carpeta>                        # captura
    python docs/rediseno/control_visual.py --salida <carpeta> --comparar <otra>      # captura y compara

Sale con 1 si hay scroll horizontal, errores de JS o, con --comparar --igual, algún píxel distinto.
Las capturas van fuera del repo salvo que la etapa las pida; nunca con datos reales (usar config/ejemplo).
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ANCHOS = (360, 375, 1200)
VISTAS = ("manual", "radar", "portales", "perfil", "tickets")
# Sin animaciones ni cursor: dos corridas iguales dan los mismos píxeles
QUIETO = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"


def _vista(page, vista):
    if vista == "portales":
        if not page.is_visible("#btnVistaPortales"):
            return False
        page.click("#btnVistaPortales")
        page.wait_for_selector("#vista-radar[data-modo=portales]:not([hidden])")
    elif vista == "tickets" and not page.is_visible("#btnVistaTickets"):
        page.click("#btnReportarError")
        page.click("#btnTicketVerTodos")
        page.wait_for_selector("#vista-tickets:not([hidden])")
    else:
        page.click(f"#btnVista{vista.capitalize()}")
        if vista != "manual":
            page.wait_for_selector(f"#vista-{vista}:not([hidden])")
    page.wait_for_timeout(800)
    return True


def capturar(base: str, salida: Path):
    salida.mkdir(parents=True, exist_ok=True)
    problemas = []
    with sync_playwright() as pw:
        try:
            nav = pw.chromium.launch(headless=True, channel="msedge")
        except Exception:
            nav = pw.chromium.launch(headless=True)
        for ancho in ANCHOS:
            ctx = nav.new_context(viewport={"width": ancho, "height": 900}, device_scale_factor=1)
            page = ctx.new_page()
            errores = []
            page.on("pageerror", lambda e: errores.append(str(e)))
            page.on("dialog", lambda d: d.dismiss())
            page.goto(base + "/", wait_until="load")
            page.wait_for_function("document.readyState === 'complete' && !!document.getElementById('btnVistaRadar')")
            page.add_style_tag(content=QUIETO)
            for vista in VISTAS:
                if not _vista(page, vista):
                    continue
                desborde = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                nombre = f"{vista}-{ancho}"
                page.screenshot(path=str(salida / f"{nombre}.png"))
                estado = "OK" if desborde <= 0 else f"SCROLL HORIZONTAL {desborde}px"
                if desborde > 0:
                    problemas.append(nombre)
                print(f"{estado:<6} {nombre}")
            if errores:
                problemas.append(f"errores JS a {ancho}px: {errores[:3]}")
                print(f"ERROR JS a {ancho}px: {errores[:3]}")
            ctx.close()
        nav.close()
    return problemas


def comparar(a: Path, b: Path):
    from PIL import Image, ImageChops
    distintas = []
    for img in sorted(b.glob("*.png")):
        otra = a / img.name
        if not otra.exists():
            distintas.append((img.name, "falta en la referencia"))
            continue
        x, y = Image.open(otra).convert("RGB"), Image.open(img).convert("RGB")
        if x.size != y.size:
            distintas.append((img.name, f"tamaño {x.size} contra {y.size}"))
            continue
        caja = ImageChops.difference(x, y).getbbox()
        if caja:
            n = sum(1 for p in ImageChops.difference(x, y).getdata() if p != (0, 0, 0))
            distintas.append((img.name, f"{n} píxeles distintos en {caja}"))
    for nombre, detalle in distintas:
        print(f"DISTINTA {nombre}: {detalle}")
    print(f"{len(list(b.glob('*.png')))} capturas comparadas, {len(distintas)} distintas")
    return distintas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:1199")
    ap.add_argument("--salida", required=True, type=Path)
    ap.add_argument("--comparar", type=Path, help="carpeta de otra corrida para contar píxeles distintos")
    ap.add_argument("--igual", action="store_true", help="con --comparar: falla si alguna captura difiere")
    args = ap.parse_args()
    if ":1111" in args.base:
        print("El 1111 es producción: el control visual va contra el 1199.")
        return 2
    problemas = capturar(args.base, args.salida)
    distintas = comparar(args.comparar, args.salida) if args.comparar else []
    return 1 if problemas or (args.igual and distintas) else 0


if __name__ == "__main__":
    sys.exit(main())
