"""Tests de mensajes y botones claros en el Radar y en Manual (Feature: ux-mensajes)."""
import pytest
from pathlib import Path
from bs4 import BeautifulSoup
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

import server
from core import radar_store
from core.procesos import _armar_item, armar_procesos
from core.radar import RadarLoop
from tests.test_radar_ui import _node, NODE_DOM_FALSO, requiere_node


# Completa eventos/atributos del DOM falso sin modificar el arnés compartido.
DOM_EVENTOS = NODE_DOM_FALSO + r"""
const obtenerNodo = document.getElementById;
const completarNodo = (n) => {
  if (n.completo) return n;
  n.completo = true;
  n.dataset = {};
  n.handlers = n.handlers || {};
  n.addEventListener = (tipo, fn) => { n.handlers[tipo] = fn; };
  n.removeAttribute = (k) => { delete n[k]; };
  const append = n.appendChild.bind(n);
  n.appendChild = (c) => c.fragmento ? c.children.slice().forEach(append) : append(c);
  return n;
};
document.getElementById = (id) => completarNodo(obtenerNodo(id));
const crearNodo = document.createElement;
document.createElement = (tag) => completarNodo(crearNodo(tag));
document.createDocumentFragment = () => {
  const f = completarNodo(falso('fragment'));
  f.fragmento = true;
  return f;
};
global.setInterval = () => 0;
"""


# ---------------------------------------------------------------------------
# Punto 1: Pill de estado con estado ERROR
# ---------------------------------------------------------------------------

@requiere_node
def test_pill_error_con_error_de_red_y_next_cycle_at():
    out = _node("""
const pill = U.describirPill({
  estado: 'ERROR',
  estado_detalle: 'net::ERR_INTERNET_DISCONNECTED at https://linkedin.com',
  next_cycle_at: '2026-11-06 15:30:00'
}, false);
console.log(JSON.stringify(pill));
""")
    assert out["texto"] == "Sin conexión a internet. Reintenta a las 15:30"
    assert out["title"] == "net::ERR_INTERNET_DISCONNECTED at https://linkedin.com"
    assert "ERR_INTERNET_DISCONNECTED" not in out["texto"]


@requiere_node
@pytest.mark.parametrize("clave_red", [
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_REFUSED",
    "net::ERR_TIMED_OUT",
])
def test_pill_error_red_sin_next_cycle_at(clave_red):
    out = _node(f"""
const pill = U.describirPill({{
  estado: 'ERROR',
  estado_detalle: 'Fallo con {clave_red}',
  next_cycle_at: null
}}, false);
console.log(JSON.stringify(pill));
""")
    assert out["texto"] == "Sin conexión a internet. Reintenta en el próximo ciclo"
    assert out["title"] == f"Fallo con {clave_red}"


@requiere_node
def test_pill_error_otro_error_con_y_sin_next_cycle_at():
    out = _node("""
const conHora = U.describirPill({
  estado: 'ERROR',
  estado_detalle: 'selectores rotos en el feed',
  next_cycle_at: '2026-11-06 16:45:00'
}, false);
const sinHora = U.describirPill({
  estado: 'ERROR',
  estado_detalle: 'selectores rotos en el feed',
  next_cycle_at: null
}, false);
console.log(JSON.stringify({ conHora, sinHora }));
""")
    assert out["conHora"]["texto"] == "Error del Radar. Reintenta a las 16:45"
    assert out["conHora"]["title"] == "selectores rotos en el feed"
    assert "selectores rotos" not in out["conHora"]["texto"]

    assert out["sinHora"]["texto"] == "Error del Radar. Reintenta en el próximo ciclo"
    assert out["sinHora"]["title"] == "selectores rotos en el feed"
    assert "selectores rotos" not in out["sinHora"]["texto"]


@requiere_node
def test_detalle_tecnico_solo_en_title_y_se_limpia_al_recuperarse():
    out = _node(DOM_EVENTOS + """
T.S.iniciado = true;
window.Radar.onStatus({ estado: 'ERROR', estado_detalle: '<error técnico>' });
const pill = nodos.radarEstadoPill;
const error = { texto: pill.textContent, title: pill.title };
window.Radar.onStatus({ estado: 'CORRIENDO' });
console.log(JSON.stringify({ error, titleFinal: pill.title }));
""")
    assert out["error"]["texto"] == "Error del Radar. Reintenta en el próximo ciclo"
    assert out["error"]["title"] == "<error técnico>"
    assert out["titleFinal"] == ""


# ---------------------------------------------------------------------------
# Punto 2: Botón «Reintentar ahora» y POST /api/radar/reintentar
# ---------------------------------------------------------------------------

@pytest.fixture
def radar_mock(monkeypatch):
    stub = AsyncMock(spec=RadarLoop)
    monkeypatch.setattr(server, "radar_loop", stub)
    return stub


@pytest.fixture
def api_client(radar_mock):
    return TestClient(server.app)


def test_api_reintentar_reinicia_radar_y_devuelve_status(api_client, radar_mock):
    # Radar en estado ERROR simulado
    radar_store.update_radar_state(desired_running=False)
    r = api_client.post("/api/radar/reintentar")
    assert r.status_code == 200
    data = r.json()
    assert data["desired_running"] is True
    radar_mock.stop.assert_awaited_once()
    radar_mock.start.assert_awaited_once()


def test_api_reintentar_espera_detencion_antes_de_arrancar(api_client, radar_mock):
    pasos = []

    async def detener():
        pasos.append("detenido")
        radar_store.update_radar_state(desired_running=False, estado="DETENIDO")

    async def iniciar():
        assert pasos == ["detenido"]
        assert radar_store.get_radar_state()["desired_running"] is True
        pasos.append("iniciado")
        radar_store.update_radar_state(estado="CORRIENDO", next_cycle_at=None)

    radar_mock.stop.side_effect = detener
    radar_mock.start.side_effect = iniciar
    radar_store.update_radar_state(estado="ERROR", next_cycle_at="2026-11-06 15:30:00")
    respuesta = api_client.post("/api/radar/reintentar")
    assert respuesta.status_code == 200
    assert pasos == ["detenido", "iniciado"]
    assert respuesta.json()["estado"] == "CORRIENDO"
    assert respuesta.json()["next_cycle_at"] is None


@requiere_node
def test_boton_reintentar_ahora_visibilidad_segun_estado_y_clic():
    out = _node(DOM_EVENTOS + """
const llamadas = [];
global.fetch = async (url, opts) => {
  llamadas.push(((opts && opts.method) || 'GET') + ' ' + url);
  const data = url.includes('/posts') ? [] : { estado: 'CORRIENDO', desired_running: true };
  return { ok: true, status: 200, json: async () => data };
};
(async () => {
await window.Radar.init();
T.S.modo = 'radar';

// 1. Estado CORRIENDO: botón oculto
window.Radar.onStatus({ estado: 'CORRIENDO', desired_running: true });
const ocultoCorriendo = nodos.radarReintentarAhoraBtn.hidden;

// 2. Estado ERROR: botón visible
window.Radar.onStatus({ estado: 'ERROR', estado_detalle: 'net::ERR_CONNECTION_RESET', desired_running: true });
const visibleError = !nodos.radarReintentarAhoraBtn.hidden;

// 3. Estado ERROR pero modo manual: botón oculto
T.S.modo = 'manual';
window.Radar.onStatus({ estado: 'ERROR', estado_detalle: 'net::ERR_CONNECTION_RESET', desired_running: true });
const ocultoEnManual = nodos.radarReintentarAhoraBtn.hidden;

// 4. Volver a radar y hacer clic en el botón
T.S.modo = 'radar';
window.Radar.onStatus({ estado: 'ERROR', estado_detalle: 'net::ERR_CONNECTION_RESET', desired_running: true });
  const handler = nodos.radarReintentarAhoraBtn.handlers && nodos.radarReintentarAhoraBtn.handlers.click;
  await handler();
  console.log(JSON.stringify({
    ocultoCorriendo,
    visibleError,
    ocultoEnManual,
    llamadas
  }));
  process.exit(0);
})();
""")
    assert out["ocultoCorriendo"] is True
    assert out["visibleError"] is True
    assert out["ocultoEnManual"] is True
    assert "POST /api/radar/reintentar" in out["llamadas"]


@requiere_node
def test_reintentar_se_oculta_al_volver_a_radar_sin_error():
    out = _node(DOM_EVENTOS + """
document.querySelectorAll = (sel) => sel === '[data-solo-radar]'
  ? [document.getElementById('radarReintentarAhoraBtn')] : [];
T.S.estado = { estado: 'CORRIENDO' };
T.S.modo = 'manual';
T.aplicarModo();
T.S.modo = 'radar';
T.aplicarModo();
console.log(JSON.stringify({ oculto: nodos.radarReintentarAhoraBtn.hidden }));
""")
    assert out["oculto"] is True


# ---------------------------------------------------------------------------
# Punto 3: Vista Manual con 0 ofertas manuales
# ---------------------------------------------------------------------------

@requiere_node
def test_vista_manual_con_cero_ofertas_muestra_explicacion_y_abre_historial():
    out = _node(DOM_EVENTOS + """
T.S.iniciado = true;
T.S.modo = 'manual';
T.S.posts.clear();
let posts = [];
global.fetch = async () => ({ ok: true, json: async () => posts });

// Sin ofertas manuales
(async () => {
await T.cargarPosts();
const vacio = nodos.radarListaVacia;
const textoVacio = vacio.textContent || '';
const historial = nodos.manualHistorial;
const historialAbierto = Boolean(historial && historial.open);
const parrafos = vacio.children.map(p => p.textContent);

// Con >= 1 oferta manual
posts = [{ id: 10, fuente: 'manual', estado: 'LISTO' }];
await T.cargarPosts();
const vacioConOferta = vacio.hidden;

console.log(JSON.stringify({
  textoVacio,
  historialAbierto,
  vacioConOferta,
  conservaHistorialAbierto: historial.open,
  parrafos
}));
process.exit(0);
})();
""")
    assert "Todavía no cargaste ofertas. Tocá «Nueva oferta» para pegar un texto o un link." in out["textoVacio"]
    assert "Tus postulaciones anteriores están en «Historial anterior»." in out["textoVacio"]
    assert out["historialAbierto"] is True
    assert out["vacioConOferta"] is True
    assert out["conservaHistorialAbierto"] is True
    assert out["parrafos"] == [
        "Todavía no cargaste ofertas. Tocá «Nueva oferta» para pegar un texto o un link.",
        "Tus postulaciones anteriores están en «Historial anterior».",
    ]


@requiere_node
def test_botones_indican_cv_y_mail():
    out = _node(DOM_EVENTOS + """
const card = T.crearCard({ id: 1, estado: 'ANALIZADA' });
console.log(JSON.stringify({ html: card.innerHTML }));
""")
    card = BeautifulSoup(out["html"], "html.parser")
    assert card.select_one('[data-accion="generar-cv"]').get_text(strip=True) == "CV y mail"
    pagina = BeautifulSoup(Path("static/index.html").read_text(encoding="utf-8"), "html.parser")
    assert pagina.select_one("#radarGenerarCvBtn").get_text(strip=True) == "Generar CV y mail"


# ---------------------------------------------------------------------------
# Punto 5: Historial de procesos con fallback de puesto a «Oferta de <autor>»
# ---------------------------------------------------------------------------

def test_armar_item_fallback_puesto_analisis_y_clasificacion():
    # Sin puesto pero con autor
    post_analisis = {"id": 1, "autor": "Laura Gómez", "estado": "ANALIZANDO"}
    item_a = _armar_item(post_analisis, tipo="analisis")
    assert item_a["puesto"] == "Oferta de Laura Gómez"

    post_clasif = {"id": 2, "autor": "Carlos Ruiz", "estado": "NUEVO"}
    item_c = _armar_item(post_clasif, tipo="clasificacion")
    assert item_c["puesto"] == "Oferta de Carlos Ruiz"

    # Con puesto definido, no usa el fallback
    post_con_puesto = {"id": 3, "autor": "Carlos Ruiz", "puesto": "Tech Lead", "estado": "ANALIZANDO"}
    item_p = _armar_item(post_con_puesto, tipo="analisis")
    assert item_p["puesto"] == "Tech Lead"

    # Con puesto en triage, no usa el fallback
    post_con_triage = {"id": 4, "autor": "Carlos Ruiz", "triage": {"puesto": "Data Engineer"}, "estado": "ANALIZANDO"}
    item_t = _armar_item(post_con_triage, tipo="analisis")
    assert item_t["puesto"] == "Data Engineer"

    # Tipo generacion sin puesto no usa "Oferta de ..."
    post_gen = {"id": 5, "autor": "Carlos Ruiz", "estado": "GENERANDO"}
    item_g = _armar_item(post_gen, tipo="generacion")
    assert item_g["puesto"] == ""


def test_armar_procesos_en_curso_muestra_oferta_de_autor_para_en_vuelo(monkeypatch):
    class StubRadar:
        generando_ahora = None
        en_vuelo = {101}

    # Post 101 en vuelo para análisis sin puesto aún
    monkeypatch.setattr(
        radar_store,
        "get_post",
        lambda pid: {"id": 101, "autor": "María González", "estado": "ANALIZANDO", "fuente": "feed"} if pid == 101 else None
    )
    resultado = armar_procesos(StubRadar())
    assert len(resultado["en_curso"]) == 1
    assert resultado["en_curso"][0]["puesto"] == "Oferta de María González"
