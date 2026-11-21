"""Abre Edge con el perfil de una cuenta de LinkedIn del Radar para que el usuario inicie sesión una vez.

    python tools/login_cuenta.py            # cuenta 2: <DATA_DIR>/linkedin_profile_2
    python tools/login_cuenta.py --principal

El usuario escribe su usuario y contraseña en la ventana de Edge: este script nunca los pide ni los guarda. La sesión
(cookie) queda en la carpeta del perfil, que no se versiona. Cuando detecta la sesión iniciada, deja la marca que usa
el Radar para habilitar esa cuenta y cierra la ventana. Correrlo con el Radar detenido: un perfil no se puede abrir en
dos Edge a la vez.
"""
import sys
import time  # noqa: F401
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.linkedin_harvester import _is_authenticated_linkedin_url  # noqa: E402
from core.paths import DATA_DIR, marcar_sesion  # noqa: E402

FEED_URL = "https://www.linkedin.com/feed/"
ESPERA_MAXIMA_S = 15 * 60


def _sesion_iniciada(contexto) -> bool:
    """Hay sesión si existe la cookie de sesión de LinkedIn («li_at») o alguna pestaña está en una página interna."""
    try:
        if any(c.get("name") == "li_at" and c.get("value") for c in contexto.cookies("https://www.linkedin.com")):
            return True
    except Exception:
        pass
    return any(_is_authenticated_linkedin_url(p.url) for p in contexto.pages if not p.is_closed())


def main() -> int:
    principal = "--principal" in sys.argv
    perfil = DATA_DIR / ("linkedin_profile" if principal else "linkedin_profile_2")
    from playwright.sync_api import sync_playwright
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        contexto = pw.chromium.launch_persistent_context(
            user_data_dir=str(perfil), channel="msedge", headless=False, chromium_sandbox=True,
            viewport={"width": 1280, "height": 900})
        page = contexto.pages[0] if contexto.pages else contexto.new_page()
        # Si la sesión ya estaba guardada, LinkedIn abre el inicio directamente
        page.goto(FEED_URL, wait_until="domcontentloaded")
        page.bring_to_front()
        print(f"Iniciá sesión en la ventana de Edge (perfil {perfil.name}). Espero hasta 15 minutos.", flush=True)
        limite = time.monotonic() + ESPERA_MAXIMA_S
        while time.monotonic() < limite:
            if _sesion_iniciada(contexto):
                marcar_sesion(perfil, datetime.now().isoformat(timespec="seconds"))
                print(f"Sesión iniciada y guardada en {perfil}.", flush=True)
                contexto.close()
                return 0
            # Espera procesando los eventos del navegador (time.sleep no los procesa: la URL no se actualizaba)
            page = next((p for p in contexto.pages if not p.is_closed()), None) or contexto.new_page()
            page.wait_for_timeout(3000)
        contexto.close()
    print("No se detectó el inicio de sesión a tiempo. Volvé a correrlo.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
