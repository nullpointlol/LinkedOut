"""Rediseño E0: base de tokens, componentes e íconos, junto a Tailwind.

Medidas:
- cada par de tokens de texto o de interfaz llega a su mínimo AA (tools/contraste_tokens.py);
- componentes.css usa solo tokens definidos, ningún selector global y ninguna clase de los estilos viejos;
- el sprite de íconos sale tal cual de los Lucide copiados en static/vendor (versión fija, sin CDN);
- el portal carga tokens.css antes que componentes.css, y los dos se sirven sin depender de la red.
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

import server
from tools import armar_iconos, contraste_tokens

RAIZ = Path(__file__).resolve().parent.parent
CSS = RAIZ / "static" / "css"
INDEX = RAIZ / "static" / "index.html"


def _sin_comentarios(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _selectores(css: str):
    """Selectores de cada regla (sin @media ni @layer), separados por coma."""
    css = _sin_comentarios(css)
    for bloque in re.findall(r"([^{}@]+)\{[^{}]*\}", css):
        for sel in bloque.split(","):
            if sel.strip():
                yield sel.strip()


# ---------------------------------------------------------------- tokens

def test_cada_par_de_tokens_llega_a_su_minimo_aa():
    tokens = contraste_tokens.leer_tokens()
    fallas = [(f, b, round(contraste_tokens.contraste(tokens[f], tokens[b]), 2), m)
              for f, b, m, _ in contraste_tokens.PARES if contraste_tokens.contraste(tokens[f], tokens[b]) < m]
    assert not fallas


def test_tokens_css_solo_define_variables_en_root():
    css = _sin_comentarios((CSS / "tokens.css").read_text(encoding="utf-8"))
    assert set(_selectores(css)) == {":root"}
    # Sin color-scheme ni nada que cambie el portal actual: eso llega con el marco (E1)
    assert "color-scheme" not in css


def test_ningun_otro_estilo_redefine_los_tokens():
    """tokens.css es la fuente única: ni index.html ni los estilos que arma el JS asignan esas variables."""
    nuestros = set(re.findall(r"--([a-z0-9-]+)\s*:", (CSS / "tokens.css").read_text(encoding="utf-8")))
    portal = "".join(p.read_text(encoding="utf-8") for p in (RAIZ / "static").glob("*.js")) + INDEX.read_text(encoding="utf-8")
    assert not nuestros & set(re.findall(r"--([a-z0-9-]+)\s*:", portal))


# ---------------------------------------------------------------- componentes

def test_componentes_usa_solo_tokens_definidos():
    definidos = set(re.findall(r"--([a-z0-9-]+)\s*:", (CSS / "tokens.css").read_text(encoding="utf-8")))
    usados = set(re.findall(r"var\(--([a-z0-9-]+)", (CSS / "componentes.css").read_text(encoding="utf-8")))
    assert usados and usados <= definidos, usados - definidos


def test_componentes_sin_selectores_globales():
    """Cada selector arranca por una clase: ni elementos sueltos, ni *, ni [hidden], ni body (eso va en E1)."""
    css = (CSS / "componentes.css").read_text(encoding="utf-8")
    globales = [s for s in _selectores(css) if "." not in re.split(r"[\s>+~]+", s)[0]]
    assert not globales


def test_componentes_sin_capa_para_ganarle_al_preflight_de_tailwind():
    """En una @layer, el preflight de Tailwind (sin capa) pisaría reglas como el fondo de .btn-primary."""
    assert "@layer" not in _sin_comentarios((CSS / "componentes.css").read_text(encoding="utf-8"))


def _css_viejo() -> str:
    """Los estilos que conviven con el rediseño: los <style> de index.html y los que arma el JS en plantillas."""
    html = INDEX.read_text(encoding="utf-8")
    css = "".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    for js in (RAIZ / "static").glob("*.js"):
        css += "".join(t for t in re.findall(r"`([^`]*)`", js.read_text(encoding="utf-8"), re.S)
                       if re.search(r"[#.][\w-]+[^{}]*\{[^{}]*:[^{}]*\}", t))
    return css


def test_componentes_no_choca_con_los_estilos_viejos():
    """Mientras conviven (E0–E4), ninguna clase de componentes.css está definida también en los estilos viejos."""
    nuestras = set(re.findall(r"\.([a-z][\w-]*)", _sin_comentarios((CSS / "componentes.css").read_text(encoding="utf-8"))))
    viejas = set()
    for bloque in re.findall(r"([^{}]+)\{[^{}]*\}", _sin_comentarios(_css_viejo())):
        viejas |= set(re.findall(r"\.([a-z][\w-]*)", bloque))
    assert not nuestras & viejas, sorted(nuestras & viejas)


# ---------------------------------------------------------------- íconos

def test_sprite_de_iconos_al_dia_con_los_lucide_copiados():
    assert (RAIZ / "static" / "iconos.svg").read_text(encoding="utf-8") == armar_iconos.armar()
    # Los mismos íconos como máscaras CSS (listas largas del Radar)
    assert (RAIZ / "static" / "css" / "iconos.css").read_text(encoding="utf-8") == armar_iconos.armar_css()


def test_sprite_un_simbolo_por_icono_con_trazo_del_plan():
    sprite = (RAIZ / "static" / "iconos.svg").read_text(encoding="utf-8")
    ids = re.findall(r'<symbol id="([a-z0-9-]+)"', sprite)
    assert ids and len(ids) == len(set(ids)) == len(list(armar_iconos.ICONOS.glob("*.svg")))
    assert sprite.count('stroke-width="1.75"') == len(ids)
    assert "@license lucide-static" in sprite


def test_iconos_minimos_del_prototipo():
    ids = set(re.findall(r'<symbol id="([a-z0-9-]+)"', (RAIZ / "static" / "iconos.svg").read_text(encoding="utf-8")))
    assert {"x", "check", "plus", "search", "mail", "send", "trash-2", "refresh-cw", "user", "ticket", "briefcase",
            "ellipsis", "chevron-down", "circle-check", "circle-alert", "triangle-alert", "save", "menu"} <= ids


# ---------------------------------------------------------------- portal

def test_portal_carga_tokens_antes_que_componentes():
    html = INDEX.read_text(encoding="utf-8")
    t = html.find('href="/static/css/tokens.css')
    c = html.find('href="/static/css/componentes.css')
    assert 0 < t < c


def test_css_e_iconos_se_sirven_localmente():
    cliente = TestClient(server.app)
    for ruta, tipo in (("/static/css/tokens.css", "text/css"), ("/static/css/componentes.css", "text/css"),
                       ("/static/iconos.svg", "image/svg+xml")):
        r = cliente.get(ruta)
        assert r.status_code == 200 and r.headers["content-type"].startswith(tipo), ruta
