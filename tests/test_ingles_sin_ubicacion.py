"""Oferta en inglés sin señal de que acepte candidatos del país del perfil: no apta por ubicación.

Candidato inventado (perfil de `config/ejemplo/` y variantes ficticias); sin portal, Radar ni datos reales.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core import descartes, radar_store
from core.descartes import MOTIVO_INGLES_SIN_UBICACION, condicion_ubicacion
from core.idioma import escrita_en_ingles
from core.radar import RadarLoop
from tests.test_brechas_api_aprendizaje import entorno, requisito  # noqa: F401 (fixture)

PERFIL = json.loads((Path(__file__).resolve().parent.parent / "config" / "ejemplo" / "candidato"
                     / "restricciones.json").read_text(encoding="utf-8"))

# El caso que encontró el creador: post de una HRBP, en inglés, sin ninguna ubicación, CV por DM
CASO_DM = (
    "We're hiring! Digital Workspace Officer (IT Helpdesk) for a confidential client. "
    "You will be the first line of support for our end users, managing tickets, onboarding devices "
    "and keeping the digital workplace running. We are looking for someone with 2+ years of experience "
    "in service desk roles and strong communication skills. If you are interested, send me your CV by DM."
)
ES_SIN_UBICACION = (
    "Estamos buscando un Analista de Soporte IT para sumarse a nuestro equipo. Vas a gestionar tickets, "
    "dar soporte a los usuarios y documentar procesos. Enviá tu CV en inglés por mensaje privado."
)


def _en(sufijo):
    return CASO_DM + " " + sufijo


# ------------------------------------------------------------------ detección del idioma

def test_escrita_en_ingles_distingue_idioma_y_no_cuenta_pedir_cv_en_ingles():
    assert escrita_en_ingles(CASO_DM)
    assert not escrita_en_ingles(ES_SIN_UBICACION)  # pide CV en inglés, pero está escrita en castellano
    assert not escrita_en_ingles("")


# ------------------------------------------------------------------ la regla

def test_caso_del_creador_no_es_apto():
    assert condicion_ubicacion(CASO_DM, PERFIL) == MOTIVO_INGLES_SIN_UBICACION
    assert MOTIVO_INGLES_SIN_UBICACION == "En inglés y sin indicar que acepte candidatos de Argentina"


@pytest.mark.parametrize("senal", [
    "Location: Buenos Aires.",                       # localidad del perfil
    "Remote from Argentina.",                        # «Remoto desde» del perfil
    "Argentine applicants welcome.",                 # gentilicio
    "100% remote, LATAM.",
    "Open to candidates in Latin America.",
    "Remote - South America.",
    "This is a worldwide position.",
    "Work from anywhere.",
    "Global remote team.",
    "Open to candidates from Argentina, Chile and Uruguay.",
    "Location: Remote: PH / South Africa / LatAm.",  # «LatAm» no se parte como CamelCase
    "#BuenosAires #Hiring",
    "Working hours aligned to GMT-3.",
    "Overlap with UTC -3 required.",
])
def test_senal_compatible_la_deja_apta(senal):
    assert condicion_ubicacion(_en(senal), PERFIL) is None


@pytest.mark.parametrize("ajena", [
    "Remote, USA only.",
    "Must be based in the UK.",
    "Fully remote within Europe.",
    "Working hours aligned to EST (UTC-5).",
])
def test_ubicacion_ajena_o_solo_remoto_no_alcanza(ajena):
    assert condicion_ubicacion(_en(ajena), PERFIL) == MOTIVO_INGLES_SIN_UBICACION


def test_contacto_local_cuenta_como_senal():
    # Un dominio .ar o un teléfono +54 (la señal determinista del clasificador) es explícita: la oferta es de acá
    assert condicion_ubicacion(_en("Apply at www.escuela-ficticia.edu.ar."), PERFIL) is None
    assert condicion_ubicacion(CASO_DM, PERFIL, contacto_local="teléfono +54") is None


def test_en_castellano_nunca_cambia():
    assert condicion_ubicacion(ES_SIN_UBICACION, PERFIL) is None
    assert condicion_ubicacion("Buscamos Analista. Remoto, USA.", PERFIL) is None


def test_sin_perfil_no_inventa_restricciones():
    assert condicion_ubicacion(CASO_DM, {}) is None


def test_lo_compatible_sale_del_perfil():
    # Candidato inventado que trabaja remoto desde otro país ficticio
    perfil = {**PERFIL, "remoto_desde": "Ruritania", "localidades_permitidas": ["zenda"]}
    assert condicion_ubicacion(_en("Remote from Ruritania."), perfil) is None
    assert condicion_ubicacion(_en("Office in Zenda."), perfil) is None
    assert condicion_ubicacion(_en("Remote from Argentina."), perfil) == MOTIVO_INGLES_SIN_UBICACION
    # Los sinónimos de LATAM son el valor por defecto; el perfil los puede reemplazar
    assert condicion_ubicacion(_en("Remote, LATAM."), perfil) is None
    propio = {**perfil, "ubicaciones_compatibles": ["mitteleuropa"]}
    assert condicion_ubicacion(_en("Remote, LATAM."), propio) == MOTIVO_INGLES_SIN_UBICACION
    assert condicion_ubicacion(_en("Remote within Mitteleuropa."), propio) is None
    # Husos: por defecto GMT-3; el perfil los puede cambiar
    assert condicion_ubicacion(_en("Hours in UTC+1."), {**perfil, "husos_compatibles": [1]}) is None
    assert condicion_ubicacion(_en("Hours in UTC-3."), {**perfil, "husos_compatibles": [1]}) == MOTIVO_INGLES_SIN_UBICACION


def test_campos_nuevos_del_perfil_se_validan():
    assert descartes._validas({**PERFIL, "ubicaciones_compatibles": ["latam"], "husos_compatibles": [-3]})
    assert not descartes._validas({**PERFIL, "ubicaciones_compatibles": "latam"})
    assert not descartes._validas({**PERFIL, "ubicaciones_compatibles": [""]})
    assert not descartes._validas({**PERFIL, "husos_compatibles": ["-3"]})
    assert not descartes._validas({**PERFIL, "husos_compatibles": [True]})
    assert not descartes._validas({**PERFIL, "husos_compatibles": [20]})


# ------------------------------------------------------------------ el Radar al analizar

def _post(texto, estado="OFERTA", **extra):
    pid = radar_store.insert_post_if_new(
        {"urn": f"urn:ingles:{abs(hash((texto, estado, str(extra))))}", "texto": texto,
         "url": "https://example.test/ingles"}, query="test")
    radar_store.update_post(pid, estado=estado, email_to="rrhh@example.test", email_subject="Postulación",
                            email_body="Hola", **extra)
    return pid


async def _analizar(stub, pid, monkeypatch):
    monkeypatch.setattr("core.radar.aviso_util", lambda post: True)  # la captura no es lo que se prueba
    original = radar_store.get_post(pid)
    stub._sigue_en = RadarLoop._sigue_en
    stub._preparar_lectura = AsyncMock(return_value=original)
    stub._prepare_fn = None
    stub._analyze_fn = AsyncMock(return_value={"requisitos": [requisito("Docker", estado="MATCH_DIRECTO")],
                                               "match_score": 100})
    await RadarLoop._analizar(stub, original)
    return radar_store.get_post(pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("fuente", [None, "jooble"])
async def test_radar_y_portales_la_dejan_no_apta(entorno, fuente, monkeypatch):  # noqa: F811
    _, stub = entorno
    pid = _post(CASO_DM, **({"fuente": fuente} if fuente else {}))
    saved = await _analizar(stub, pid, monkeypatch)
    assert saved["estado"] == "IGNORADA"
    assert saved["analisis"]["no_apto"]["desde"] == "ANALIZADA"
    assert saved["analisis"]["no_apto"]["motivos"] == [
        {"requisito": "Ubicación", "categoria": "UBICACION", "texto": MOTIVO_INGLES_SIN_UBICACION}]
    assert saved["analisis"]["condicion_ubicacion"] == MOTIVO_INGLES_SIN_UBICACION


@pytest.mark.asyncio
async def test_carga_manual_queda_marcada_como_condicion(entorno, monkeypatch):  # noqa: F811
    _, stub = entorno
    pid = _post(CASO_DM, fuente=radar_store.FUENTE_MANUAL)
    saved = await _analizar(stub, pid, monkeypatch)
    assert saved["estado"] == "ANALIZADA"
    assert "no_apto" not in saved["analisis"]
    assert saved["analisis"]["condicion_ubicacion"] == MOTIVO_INGLES_SIN_UBICACION


@pytest.mark.asyncio
@pytest.mark.parametrize("texto", [ES_SIN_UBICACION, _en("Remote, LATAM.")])
async def test_castellano_o_con_ubicacion_no_cambian(entorno, texto, monkeypatch):  # noqa: F811
    _, stub = entorno
    saved = await _analizar(stub, _post(texto), monkeypatch)
    assert saved["estado"] == "ANALIZADA"
    assert "no_apto" not in saved["analisis"] and "condicion_ubicacion" not in saved["analisis"]


def test_recalcular_conserva_el_motivo_y_postular_igual_lo_respeta(entorno):  # noqa: F811
    client, _ = entorno
    analisis = {"requisitos": [requisito("Docker")], "match_score": 40,
                "condicion_ubicacion": MOTIVO_INGLES_SIN_UBICACION}
    pid = _post(CASO_DM, estado="LISTO", analisis=analisis)
    # Un clic en un requisito recalcula el descarte: el motivo por idioma no se pierde
    assert client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "Docker"}).status_code == 200
    saved = radar_store.get_post(pid)
    assert saved["estado"] == "IGNORADA"
    assert saved["analisis"]["no_apto"]["motivos"][0]["texto"] == MOTIVO_INGLES_SIN_UBICACION
    # «Postular igual» es la decisión del creador: otro recálculo no vuelve a archivarla
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 200
    assert client.post(f"/api/radar/posts/{pid}/requisito", json={"nombre": "Docker"}).status_code == 200
    saved = radar_store.get_post(pid)
    assert "no_apto" not in saved["analisis"] and saved["estado"] == "LISTO"
    assert saved["analisis"]["ubicacion_aceptada"] is True


def test_resumen_expone_la_condicion(entorno):  # noqa: F811
    client, _ = entorno
    pid = _post(CASO_DM, estado="ANALIZADA", fuente=radar_store.FUENTE_MANUAL,
                analisis={"requisitos": [requisito()], "condicion_ubicacion": MOTIVO_INGLES_SIN_UBICACION})
    fila = next(p for p in client.get("/api/radar/posts?estado=ANALIZADA&fuente=manual").json() if p["id"] == pid)
    assert fila["condicion_ubicacion"] == MOTIVO_INGLES_SIN_UBICACION


# ------------------------------------------------------------------ re-chequeo de lo ya analizado

def test_rechequeo_cuenta_y_aplica_sin_perder_ofertas(entorno):  # noqa: F811
    from core.rechequeo_ubicacion import rechequear

    base = {"requisitos": [requisito("Docker", estado="MATCH_DIRECTO")], "match_score": 100}
    ids = {
        "listo": _post(CASO_DM, estado="LISTO", analisis=dict(base)),
        "analizada": _post(_en("Remote, USA only."), estado="ANALIZADA", analisis=dict(base)),
        "aprobada": _post(_en("Apply now."), estado="LISTO", analisis=dict(base), aprobado_at="2026-11-18 10:00:00"),
        "manual": _post(_en("Manual."), estado="LISTO", analisis=dict(base), fuente=radar_store.FUENTE_MANUAL),
        "castellano": _post(ES_SIN_UBICACION, estado="LISTO", analisis=dict(base)),
        "latam": _post(_en("Remote, LATAM."), estado="ANALIZADA", analisis=dict(base)),
        "enviada": _post(_en("Sent."), estado="ENVIADO", analisis=dict(base)),
    }
    antes = {k: radar_store.get_post(v) for k, v in ids.items()}

    simulado = rechequear(aplicar=False)
    assert simulado["cambiarian"] == 2 and sorted(simulado["ids"]) == sorted([ids["listo"], ids["analizada"]])
    assert simulado["solo_condicion"] == 2  # la aprobada y la manual: quedan marcadas, no se archivan
    assert all(radar_store.get_post(v) == antes[k] for k, v in ids.items())  # simular no escribe

    aplicado = rechequear(aplicar=True)
    assert aplicado["cambiarian"] == 2
    for clave, desde in (("listo", "LISTO"), ("analizada", "ANALIZADA")):
        post = radar_store.get_post(ids[clave])
        assert post["estado"] == "IGNORADA" and post["analisis"]["no_apto"]["desde"] == desde
        assert post["analisis"]["match_score"] == 100
    for clave in ("aprobada", "manual"):
        post = radar_store.get_post(ids[clave])
        assert post["estado"] == "LISTO" and "no_apto" not in post["analisis"]
        assert post["analisis"]["condicion_ubicacion"] == MOTIVO_INGLES_SIN_UBICACION
    for clave in ("castellano", "latam", "enviada"):
        assert radar_store.get_post(ids[clave]) == antes[clave]
    assert all(radar_store.get_post(v) is not None for v in ids.values())  # no se borra ninguna
    assert rechequear(aplicar=True)["cambiarian"] == 0  # idempotente


# ------------------------------------------------------------------ la condición a la vista

def test_ui_muestra_la_condicion_y_no_la_premarca():
    from tests.test_brechas_radar_ui import node

    out = node("""
const cond = 'En inglés y sin indicar que acepte candidatos de Argentina';
const manual = {id: 9, fuente: 'manual', estado: 'LISTO', updated_at: 'v1', match_score: 100, puesto: 'Officer',
  triage: {alcance: 'ARGENTINA'}, no_apto: null, condicion_ubicacion: cond,
  analisis: {condicion_ubicacion: cond, requisitos: []}};
const sinCondicion = {...manual, id: 10, condicion_ubicacion: '', analisis: {requisitos: []}};
const card = T.crearCard(manual);
const limpia = T.crearCard(sinCondicion);
console.log(JSON.stringify({
  sello: find(card, 'data-condicion').map(n => n.textContent),
  limpia: find(limpia, 'data-condicion').length,
  con: T.datosAprobacion(manual), sin: T.datosAprobacion(sinCondicion)}));
""")
    assert out["sello"] == ["Condición: En inglés y sin indicar que acepte candidatos de Argentina"]
    assert out["limpia"] == 0
    assert out["con"]["marcada"] is False and "En inglés sin ubicación compatible" in out["con"]["alertas"]
    assert out["sin"]["marcada"] is True and out["sin"]["alertas"] == []


def test_herramienta_se_niega_con_datos_de_produccion(tmp_path, monkeypatch, capsys):
    import tools.rechequear_ubicacion as herramienta

    monkeypatch.setattr(herramienta, "DATOS_PRODUCCION", tmp_path / "prod")
    (tmp_path / "prod" / "candidato").mkdir(parents=True)
    (tmp_path / "prod" / "candidato" / "restricciones.json").write_text("{}", encoding="utf-8")
    assert herramienta.main(["--data-dir", str(tmp_path / "prod")]) == 2
    assert herramienta.main(["--data-dir", str(tmp_path / "prod" / "sub")]) == 2
    assert "producción" in capsys.readouterr().err
    assert herramienta.main(["--data-dir", str(tmp_path / "copia")]) == 2  # sin perfil: no inventa


def test_herramienta_sobre_una_copia_cuenta_sin_escribir(entorno, tmp_path, monkeypatch, capsys):  # noqa: F811
    import tools.rechequear_ubicacion as herramienta

    monkeypatch.setattr(herramienta, "DATOS_PRODUCCION", tmp_path / "prod")
    monkeypatch.setenv("LINKEDOUT_DATA_DIR", str(tmp_path))  # main la fija; monkeypatch la restaura
    (tmp_path / "copia" / "candidato").mkdir(parents=True)
    (tmp_path / "copia" / "candidato" / "restricciones.json").write_text(json.dumps(PERFIL), encoding="utf-8")
    pid = _post(CASO_DM, estado="LISTO", analisis={"requisitos": [requisito()], "match_score": 90})
    antes = radar_store.get_post(pid)
    assert herramienta.main(["--data-dir", str(tmp_path / "copia")]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["aplicado"] is False and pid in salida["ids"]
    assert radar_store.get_post(pid) == antes
