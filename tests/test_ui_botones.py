"""Inventario exacto de botones y un clic real a cada uno, con su efecto verificado.

- `tests/inventario_botones.json` lista cada botón del portal por una clave estable: los fijos de `index.html`
  (id, atributo data-* o su onclick) y los tipos que crea el JavaScript (data-req, data-accion, …). Si aparece o
  desaparece un botón sin actualizar el inventario, el test falla.
- En un navegador real, con datos de prueba, cada botón del inventario se pulsa (aunque en ese momento esté oculto
  por el estado de la pantalla: se lo muestra antes) y se verifica que haga algo: una llamada a la API, un cambio en
  la pantalla o un diálogo. Ninguno puede producir un error de JavaScript ni una respuesta 5xx.

Para regenerar el inventario después de agregar o quitar botones: `python tests/test_ui_botones.py`.
"""
import json
import time
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from core import database, radar_store
from tests.test_ui_navegador import _abrir, _sembrar_radar, _vista, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(600)]

RAIZ = Path(__file__).resolve().parent.parent
INVENTARIO = Path(__file__).resolve().parent / "inventario_botones.json"

# Botones que crea el JavaScript: (clave, selector, vista donde aparecen con los datos de prueba)
DINAMICOS = [
    ("radar:requisito", "[data-req]", "radar"),
    ("radar:me-descarta", "[data-descarte-req]", "radar"),
    ("radar:accion-tarjeta", "[data-accion]", "radar"),
    ("radar:revision-abrir", "texto:#radarRevisionLista:Abrir", "radar"),
    ("radar:revision-visto", "texto:#radarRevisionLista:Visto", "radar"),
    ("perfil:recargar", "texto:#vista-perfil:Recargar", "perfil"),
    ("perfil:agregar-area", "texto:#vista-perfil:Agregar área", "perfil"),
    ("perfil:quitar-area", "texto:#vista-perfil:Quitar", "perfil"),
    ("perfil:agregar-area-trayectoria", "texto:#vista-perfil:Agregar área de trayectoria", "perfil"),
    ("perfil:agregar-coincidencia", "texto:#vista-perfil:Agregar coincidencia", "perfil"),
    ("perfil:agregar-transferible", "texto:#vista-perfil:Agregar transferible", "perfil"),
    ("perfil:borrar-aprendizaje", "texto:#vista-perfil:Borrar", "perfil"),
    ("perfil:regenerar-listos", "texto:#vista-perfil:Regenerar CV listos sin aprobar", "perfil"),
    ("tickets:abrir", "[data-ticket-id]", "tickets"),
    ("tickets:copiar", "[data-ticket-copiar]", "tickets"),
    ("tickets:estado", "[data-ticket-estado]", "tickets"),
]
# Dinámicos que no se pueden mostrar sin IA real; quedan cubiertos por el test indicado
DINAMICOS_CUBIERTOS_EN_OTRO_TEST = {
    "manual:modelo-ia": "tests/test_model_and_tags.py (la lista de modelos la da el CLI de Antigravity)",
    "radar:deshacer": "tests/test_radar_ui.py (aparece unos segundos después de descartar)",
    "radar:ver-postulacion": "tests/test_radar_ui.py (solo con una postulación manual vinculada al post)",
    "radar:reintentar-texto": "tests/test_tarjeta_post_texto.py (solo si falla la carga del texto completo)",
    "radar:quitar-filtro": "tests/test_rediseno_linkedin.py (solo con un filtro de «Filtros» activo)",
    "perfil:ir-a-seccion": "tests/test_rediseno_perfil.py (el índice de Mi perfil solo desplaza la vista)",
    "perfil:guardar": "tests/test_perfil_ui.py y tests/test_rediseno_perfil.py («Guardar cambios» solo aparece con cambios)",
    "perfil:guardar-portales": "tests/test_rediseno_perfil.py (los portales de las búsquedas van en «Guardar cambios»)",
    "perfil:descartar": "tests/test_rediseno_perfil.py («Descartar» de la barra, solo con cambios)",
    "perfil:aviso-salir": "tests/test_rediseno_perfil.py (Seguir editando / Salir sin guardar / Guardar y salir)",
    "perfil:formulario-personales": "tests/test_rediseno_perfil.py (Ver como texto / Volver al formulario, Agregar y Quitar estudio o idioma; solo con nombre y titular o contacto)",
    "perfil:editor-experiencia": "tests/test_rediseno_perfil.py (Agregar/Quitar viñeta, cargo y empresa; Editar como texto / Volver al editor; solo con empresas «## »)",
}


def _clave(b) -> str:
    if b.get("id"):
        return "#" + b["id"]
    datos = [(k, v) for k, v in b.attrs.items() if k.startswith("data-") and k not in ("data-solo-radar", "data-solo-manual", "data-solo-linkedin", "data-solo-portales")]
    if datos:
        return '[%s="%s"]' % datos[0]
    if b.get("onclick"):
        return "onclick=" + b["onclick"]
    return "texto=" + b.get_text(" ", strip=True)


def _vista_de(b) -> str:
    for padre in b.parents:
        pid = padre.get("id") if hasattr(padre, "get") else None
        if pid in ("vista-radar", "vista-perfil", "vista-tickets"):
            return pid.split("-")[1]
        if pid == "modalNueva":
            return "manual"
    return "manual"


def inventario_actual():
    soup = BeautifulSoup((RAIZ / "static" / "index.html").read_text(encoding="utf-8"), "html.parser")
    fijos = [{"clave": _clave(b), "vista": _vista_de(b), "texto": b.get_text(" ", strip=True)[:60]}
             for b in soup.find_all("button")]
    dinamicos = [{"clave": c, "selector": s, "vista": v} for c, s, v in DINAMICOS]
    return {"fijos": fijos, "dinamicos": dinamicos, "dinamicos_cubiertos_en_otro_test": DINAMICOS_CUBIERTOS_EN_OTRO_TEST}


def test_el_inventario_coincide_con_la_pantalla():
    guardado = json.loads(INVENTARIO.read_text(encoding="utf-8"))
    actual = inventario_actual()
    claves_guardadas = [b["clave"] for b in guardado["fijos"]]
    claves_actuales = [b["clave"] for b in actual["fijos"]]
    assert len(set(claves_actuales)) == len(claves_actuales), "dos botones con la misma clave: dales un id"
    faltan = sorted(set(claves_actuales) - set(claves_guardadas))
    sobran = sorted(set(claves_guardadas) - set(claves_actuales))
    assert not faltan and not sobran, (
        f"El inventario no coincide con index.html. Nuevos: {faltan}. Ya no existen: {sobran}. "
        "Regeneralo con: python tests/test_ui_botones.py")
    assert guardado["dinamicos"] == actual["dinamicos"]


# ---------------------------------------------------------------- clic a cada botón

_CLIC_CON_EFECTO = """
async ({clave, selector}) => {
  const buscar = () => {
    if (selector && selector.startsWith('texto:')) {
      const [, cont, texto] = selector.split(':');
      const raiz = document.querySelector(cont);
      return raiz ? [...raiz.querySelectorAll('button')].find(b => b.textContent.trim() === texto) : null;
    }
    if (selector) return document.querySelector(selector);
    if (clave.startsWith('#')) return document.getElementById(clave.slice(1));
    if (clave.startsWith('[')) return document.querySelector('button' + clave);
    if (clave.startsWith('onclick=')) {
      return [...document.querySelectorAll('button')].find(b => b.getAttribute('onclick') === clave.slice(8));
    }
    return null;
  };
  const el = buscar();
  if (!el) return {encontrado: false};
  // Mostrar el botón aunque el estado actual lo oculte: se prueba su handler, no la regla de visibilidad
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    n.hidden = false;
    n.classList.remove('hidden');
  }
  el.disabled = false;
  const llamadas = [];
  const fetchOriginal = window.fetch;
  window.fetch = (...a) => { llamadas.push(String(a[0])); return fetchOriginal(...a); };
  const abiertas = [];
  const openOriginal = window.open;
  window.open = (u) => { abiertas.push(String(u)); return null; };
  let mutaciones = 0;
  const obs = new MutationObserver((l) => { mutaciones += l.length; });
  obs.observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true});
  const urlAntes = location.href;
  el.click();
  await new Promise(r => setTimeout(r, 400));
  obs.disconnect();
  window.fetch = fetchOriginal;
  window.open = openOriginal;
  return {encontrado: true, llamadas, abiertas, mutaciones, navego: location.href !== urlAntes};
}
"""


def _sembrar_todo():
    ids = _sembrar_radar(3)
    # Un post LISTO con mail para que aparezcan las acciones de envío
    radar_store.update_post(ids[0], estado="LISTO", email_to="rrhh@acme.test",
                            email_subject="Postulación Analista Funcional", email_body="Hola, adjunto mi CV.")
    database.create_postulacion(titulo="Analista Funcional", empresa="Acme", contenido="Oferta de prueba", url="",
                                origen="manual")
    from core import aprendizajes, perfil_areas, revision_store
    aprendizajes.registrar_aprendizaje("Jira", "MATCH_DIRECTO", origen="radar")
    revision_store.registrar(next(iter(revision_store.TIPOS)), campo="cv", detalle="Revisá el CV de prueba", post_id=ids[2])
    import core.paths
    (core.paths.DATA_DIR / "candidato" / perfil_areas.ARCHIVO_CREADOR).write_text(
        json.dumps({"anios_por_area": [{"area": "soporte", "anios": "3"}]}), encoding="utf-8")
    from core import tickets
    tickets.reportar_error("radar", "sin_resultados", "La búsqueda «cv a» terminó sin resultados",
                           datos={"consulta": "cv a", "cuenta": "secundaria"})
    return ids


def test_cada_boton_del_inventario_responde_con_efecto(navegador, portal):  # noqa: F811
    base, _ = portal
    ids = _sembrar_todo()
    inventario = json.loads(INVENTARIO.read_text(encoding="utf-8"))
    contexto, page, errores = _abrir(navegador, base, 1200)
    dialogos = []
    page.on("dialog", lambda d: dialogos.append(d.message))
    sin_efecto, no_encontrados, resultados = [], [], []
    try:
        botones = [dict(b, selector=None) for b in inventario["fijos"]] + inventario["dinamicos"]
        for vista in ("manual", "radar", "perfil", "tickets"):
            page.goto(base + "/", wait_until="load", timeout=30000)
            page.wait_for_function("!!document.getElementById('btnVistaRadar')")
            _vista(page, vista)
            if vista == "radar":
                page.click(f'[data-post-id="{ids[1]}"]')
                page.wait_for_selector("[data-req]", timeout=10000)
                page.evaluate("document.getElementById('radarRevisionBtn').hidden = false")
                page.click("#radarRevisionBtn")
                page.wait_for_selector("#radarRevisionLista button", timeout=10000)
            if vista == "perfil":
                page.wait_for_selector("#vista-perfil button", timeout=10000)
            if vista == "tickets":
                page.click("[data-ticket-id]")
                page.wait_for_selector("[data-ticket-copiar]", timeout=10000)
            for b in [x for x in botones if x["vista"] == vista]:
                antes = len(dialogos)
                r = page.evaluate(_CLIC_CON_EFECTO, {"clave": b["clave"], "selector": b.get("selector")})
                page.keyboard.press("Escape")
                if not r["encontrado"]:
                    no_encontrados.append(b["clave"])
                    continue
                efecto = bool(r["llamadas"] or r["abiertas"] or r["mutaciones"] or r["navego"] or len(dialogos) > antes)
                resultados.append((b["clave"], r["llamadas"][:2], r["mutaciones"], len(dialogos) > antes))
                if not efecto:
                    sin_efecto.append(b["clave"])
                if r["navego"]:
                    page.goto(base + "/", wait_until="load")
                    _vista(page, vista)
        total = len(botones)
        print(f"\nBotones del inventario: {total} · pulsados con efecto: {len(resultados) - len(sin_efecto)}"
              f" · sin efecto: {len(sin_efecto)} · no encontrados: {len(no_encontrados)}")
        assert not no_encontrados, f"botones del inventario que no aparecen con los datos de prueba: {no_encontrados}"
        assert not sin_efecto, f"botones sin ningún efecto al pulsarlos: {sin_efecto}"
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


if __name__ == "__main__":
    INVENTARIO.write_text(json.dumps(inventario_actual(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    datos = inventario_actual()
    print(f"inventario: {len(datos['fijos'])} botones fijos y {len(datos['dinamicos'])} tipos dinámicos")
