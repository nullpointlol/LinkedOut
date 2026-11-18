"""No apto archivado y recuperable; datos sintéticos, sin arrancar workers."""
from types import MethodType
from unittest.mock import AsyncMock

import pytest

from core import radar_store
from core.colas import armar_colas
from core.radar import RadarLoop
from tests.test_brechas_api_aprendizaje import entorno, post, requisito, marcar
from tests.test_brechas_radar_ui import node


@pytest.mark.asyncio
@pytest.mark.parametrize("sellado", [True, False])
async def test_analisis_archiva_solo_con_motivos(entorno, sellado):
    _, stub = entorno
    pid = post(estado="OFERTA", fuente="manual", gen_prioridad=2)
    original = radar_store.get_post(pid)
    analisis = original["analisis"]
    if sellado:
        analisis["no_apto"] = {"motivos": [{"requisito": "Lugar", "texto": "Presencial en Córdoba"}]}
    stub._sigue_en = RadarLoop._sigue_en
    stub._preparar_lectura = AsyncMock(return_value=original)
    stub._prepare_fn = None
    async def analizar(preparado):
        assert radar_store.get_post(pid)["estado"] == "ANALIZANDO"
        return analisis
    stub._analyze_fn = analizar
    await RadarLoop._analizar(stub, original)
    saved = radar_store.get_post(pid)
    assert saved["aprobado_at"] == original["aprobado_at"] is None  # invariante
    assert saved["updated_at"] >= original["updated_at"]  # invariante: versión actualizada por el store
    assert saved["estado"] == ("IGNORADA" if sellado else "ANALIZADA")
    if sellado:
        assert saved["analisis"]["no_apto"]["desde"] == "ANALIZADA"
    else:
        assert saved["gen_prioridad"] == 2


def test_listo_descarte_y_vuelta_conservan_origen(entorno):
    client, stub = entorno
    pid = post(estado="LISTO")
    for _ in range(2):
        assert marcar(client, pid, requisito()["nombre"], True).status_code == 200
        saved = radar_store.get_post(pid)
        assert saved["estado"] == "IGNORADA"
        assert saved["analisis"]["no_apto"]["desde"] == "LISTO"
    assert marcar(client, pid, requisito()["nombre"], False).status_code == 200
    saved = radar_store.get_post(pid)
    assert saved["estado"] == "LISTO" and "no_apto" not in saved["analisis"]
    assert saved["aprobado_at"] is None  # invariante
    stub.enqueue_send.assert_not_awaited()


def test_listo_aprobado_no_archiva(entorno):
    client, _ = entorno
    pid = post(estado="LISTO", aprobado_at="2026-11-09 20:00:00")
    assert marcar(client, pid, requisito()["nombre"], True).status_code == 200
    saved = radar_store.get_post(pid)
    assert saved["estado"] == "LISTO"
    assert saved["aprobado_at"] == "2026-11-09 20:00:00"


@pytest.mark.parametrize("desde", ["ANALIZADA", "LISTO", None])
def test_force_recupera_sin_aprobar(entorno, desde):
    client, stub = entorno
    pid = post(estado="IGNORADA", sello=True)
    analisis = radar_store.get_post(pid)["analisis"]
    if desde:
        analisis["no_apto"]["desde"] = desde
    radar_store.update_post(pid, analisis=analisis)
    stub.generate_cv = MethodType(RadarLoop.generate_cv, stub)
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 200
    saved = radar_store.get_post(pid)
    assert saved["estado"] == ("LISTO" if desde == "LISTO" else "GENERANDO")
    assert saved["gen_prioridad"] == 2 and "no_apto" not in saved["analisis"]
    assert saved["aprobado_at"] is None  # invariante
    assert stub.wake_generation.await_count == (0 if desde == "LISTO" else 1)
    stub.enqueue_send.assert_not_awaited()


def test_force_rechazado_conserva_ignorada(entorno):
    client, stub = entorno
    pid = post(estado="IGNORADA", sello=True)
    before = radar_store.get_post(pid)
    stub.generate_cv.side_effect = ValueError("ocupado")
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 409
    assert radar_store.get_post(pid) == before


@pytest.mark.parametrize("sello", [False, True])
def test_requisito_ignorada_y_origen_por_defecto(entorno, sello):
    client, _ = entorno
    pid = post(estado="IGNORADA", sello=sello)
    before = radar_store.get_post(pid)
    response = marcar(client, pid, requisito()["nombre"], False)
    assert response.status_code == (200 if sello else 409)
    if sello:
        assert radar_store.get_post(pid)["estado"] == "ANALIZADA"
        assert "no_apto" not in radar_store.get_post(pid)["analisis"]
    else:
        assert client.post(f"/api/radar/posts/{pid}/force").status_code == 409
        assert radar_store.get_post(pid) == before


def test_lista_sin_tope_para_no_aptos(entorno):
    client, _ = entorno
    ids = {post(estado="IGNORADA", sello=True) for _ in range(3)}
    antiguas = {post(estado="IGNORADA") for _ in range(3)}
    rows = client.get("/api/radar/posts?limite_descartados=1").json()
    presentes = {p["id"] for p in rows}
    assert ids <= presentes and len(antiguas & presentes) == 1
    assert all(p["no_apto"]["motivos"] for p in rows if p["id"] in ids)


def test_ignorada_no_entra_en_colas(entorno):
    pid = post(estado="IGNORADA", sello=True, gen_prioridad=2)
    assert pid not in {p["id"] for p in radar_store.candidatas_cola()}
    colas = armar_colas(radar_store.candidatas_cola())
    assert not colas["prioritaria"] and not colas["fondo"]


def test_ciclo_quita_ultimo_motivo_y_recupera_listo(entorno):
    client, _ = entorno
    pid = post(estado="LISTO")
    nombre = requisito()["nombre"]
    assert marcar(client, pid, nombre, True).status_code == 200
    response = client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": nombre})
    assert response.status_code == 200
    saved = radar_store.get_post(pid)
    assert saved["estado"] == "LISTO"
    assert "no_apto" not in saved["analisis"]
    assert saved["aprobado_at"] is None


def test_ui_ignorada_recuperable_y_filtros():
    out = node("""
post.estado='IGNORADA';
T.S.selId=7; T.S.detalle=post; T.renderDetalle();
const antigua={...post,no_apto:null,analisis:{}};
console.log(JSON.stringify({actions:U.accionesPara(post),old:U.accionesPara(antigua),
 controls:find(document.getElementById('radarDetallePost'),'data-descarte-req').length,
 filters:['IGNORADA','NO_APTO','RELEVANTES','TODOS','LISTO'].map(f=>U.coincideFiltro(post,f)),
 card:T.crearCard(post).innerHTML}));
""")
    assert out["actions"]["postularIgual"] and out["actions"]["descartar"]
    assert not out["old"].get("postularIgual") and not out["old"]["descartar"]
    assert out["controls"] > 0
    assert out["filters"] == [True, True, False, False, False]
    assert "publicada hace 1 mes o m?s" not in out["card"]
    assert "No apto" in out["card"]


def test_conteo_no_apto_incluye_ignoradas_con_sello():
    out = node("""
const filter=document.getElementById('radarFiltroNoApto');
const count=document.createElement('span'); count.setAttribute('data-count',''); filter.appendChild(count);
T.S.posts=new Map([[7,post],[8,{...post,id:8,estado:'IGNORADA'}],
 [9,{...post,id:9,estado:'IGNORADA',no_apto:null,analisis:{}}]]);
T.enlazarEventos(); filter.listeners.click();
console.log(JSON.stringify({count:count.textContent,selected:T.S.filtro}));
""")
    assert out == {"count": "2", "selected": "NO_APTO"}
