import json
import os
from unittest.mock import AsyncMock
import pytest
from core import radar_store as store, aprendizajes as ap
# Reusar fixtures oficiales: SQLite aislada por conftest, Radar simulado, sin lifespan.
from tests.test_radar_api import client, radar, gmail, make_post

MOTIVO = {"requisito":"Experiencia en gastronomía", "categoria":"RUBRO", "texto":"Experiencia en rubro gastronómico"}

def analisis():
    return {"match_score":90,"puesto":"Analista funcional", "no_apto":{"motivos":[dict(MOTIVO)]},
            "requisitos":[{"nombre":MOTIVO["requisito"], "texto":MOTIVO["texto"], "tipo":"REQUISITO_EXCLUYENTE",
             "estado":"BRECHA_REAL", "approved":False, "eliminatorio":True, "categoria_descarte":"RUBRO"}]}

def test_masiva_omite_con_motivo_y_no_envia(client, radar, gmail):
    pid = make_post("LISTO",analisis=analisis())
    store.update_radar_state(analisis_pausado=True,generacion_pausada=True,envios_pausados=True)
    visto=store.get_post(pid)["updated_at"]
    # invariante: sin versión no aprueba; conserva flags y estado LISTO.
    assert client.post(f"/api/radar/posts/{pid}/approve",json={}).status_code == 400
    # invariante: el sello no permite eludir la versión revisada (base actual: 409).
    assert client.post(f"/api/radar/posts/{pid}/approve",json={"updated_at":"version-obsoleta"}).status_code == 409
    assert store.get_post(pid)["aprobado_at"] is None
    radar.enqueue_send.assert_not_awaited()
    assert all(store.get_radar_state()[k] for k in ("analisis_pausado","generacion_pausada","envios_pausados"))
    if os.getenv("INVARIANT_CHECK") == "1": return
    r=client.post("/api/radar/approve-all",json={"ids":[pid],"updated_at":{str(pid):visto}})
    assert r.status_code == 200
    assert r.json()["encolados"] == []
    assert r.json()["omitidos"] == [{"id":pid,"tipo":"no_apto","motivo":"No apto: "+MOTIVO["texto"]}]
    radar.enqueue_send.assert_not_awaited()
    assert store.get_post(pid)["estado"] == "LISTO"
    assert store.get_post(pid)["aprobado_at"] is None
    assert all(store.get_radar_state()[k] for k in ("analisis_pausado","generacion_pausada","envios_pausados"))
    # El creador sí puede aprobar uno por uno con versión vigente.
    assert client.post(f"/api/radar/posts/{pid}/approve",json={"updated_at":visto}).status_code == 200
    radar.enqueue_send.assert_awaited_once_with(pid)

def test_force_limpia_solo_el_post_y_no_aprueba(client, radar):
    pid=make_post("ANALIZADA",analisis=analisis())
    otro=make_post("ANALIZADA",analisis=analisis())
    store.update_radar_state(analisis_pausado=True,generacion_pausada=True,envios_pausados=True)
    r=client.post(f"/api/radar/posts/{pid}/force")
    assert r.status_code == 200
    post=store.get_post(pid)
    assert not post["analisis"].get("no_apto")
    assert post["gen_prioridad"] == 2
    assert post["aprobado_at"] is None and post["estado"] != "DESCARTADO"
    radar.generate_cv.assert_awaited_once_with(pid)
    radar.enqueue_send.assert_not_awaited()
    assert store.get_post(otro)["analisis"]["no_apto"]
    assert all(store.get_radar_state()[k] for k in ("analisis_pausado","generacion_pausada","envios_pausados"))

def test_force_rechazado_no_borra_motivo(client, radar):
    pid=make_post("ANALIZADA",analisis=analisis())
    radar.generate_cv.side_effect=ValueError("ocupado")
    r=client.post(f"/api/radar/posts/{pid}/force")
    assert r.status_code == 409
    assert store.get_post(pid)["analisis"]["no_apto"]["motivos"] == [MOTIVO]
    # Con éxito posterior, el sello sí se limpia (defecto de hoy).
    radar.generate_cv.side_effect=None
    assert client.post(f"/api/radar/posts/{pid}/force").status_code == 200
    assert not store.get_post(pid)["analisis"].get("no_apto")

def test_lista_y_detalle_exponen_motivos(client, radar):
    pid=make_post("ANALIZADA",analisis=analisis())
    lista=client.get("/api/radar/posts").json()
    fila=next(p for p in lista if p["id"]==pid)
    assert fila["no_apto"]["motivos"] == [MOTIVO]
    assert fila["requisitos_estado"][0]["eliminatorio"] is True
    assert client.get(f"/api/radar/posts/{pid}").json()["analisis"]["no_apto"]["motivos"] == [MOTIVO]

def test_marcar_y_desmarcar_persiste_sin_ciclar(client,radar,tmp_path,monkeypatch):
    monkeypatch.setattr(ap,"APRENDIZAJES_FILE",tmp_path/"aprendizajes.json")
    import importlib.util
    assert importlib.util.find_spec("core.descartes"), "Falta confirmador"
    import core.descartes as desc
    restricciones={"base":"San Isidro, AMBA","presencial_hibrido_permitido":"AMBA (CABA y GBA)", "localidades_permitidas": ["amba", "caba", "gba", "buenos aires", "capital federal", "san martin", "vicente lopez", "san isidro", "san fernando", "tigre", "pilar", "escobar", "moron", "la matanza", "avellaneda", "lanus", "quilmes", "lomas de zamora", "tres de febrero", "san miguel", "jose c paz", "malvinas argentinas", "hurlingham", "ituzaingo", "moreno", "merlo", "berazategui", "florencio varela", "ezeiza", "esteban echeverria", "almirante brown"],
        "remoto_desde":"Argentina","viajes_frecuentes_o_interior":False,"reubicacion":False,
        "rubro_o_funcion_exigida_sin_experiencia":False,"edad":{"actual":36,"al":"2026-11-09"}}
    f=tmp_path/"restricciones.json"; f.write_text(json.dumps(restricciones),encoding="utf-8")
    monkeypatch.setattr(desc,"RESTRICCIONES_FILE",f)
    a=analisis(); a.pop("no_apto"); a["requisitos"][0]["eliminatorio"]=False
    pid=make_post("ANALIZADA",analisis=a)
    nombre=MOTIVO["requisito"]
    for valor in (True,False):
        r=client.post(f"/api/radar/posts/{pid}/requisito",json={"nombre":nombre,"eliminatorio":valor})
        assert r.status_code == 200
        nuevo=store.get_post(pid)
        req=nuevo["analisis"]["requisitos"][0]
        assert req["estado"] == "BRECHA_REAL" and req["approved"] is False
        assert req["eliminatorio"] is valor
        assert bool(nuevo["analisis"].get("no_apto")) is valor
        assert nuevo["estado"] == ("IGNORADA" if valor else "ANALIZADA") and nuevo["aprobado_at"] is None
        entrada=next(e for e in ap.obtener_aprendizajes().values() if e["tag"]==nombre)
        assert entrada["eliminatorio"] is valor
        assert nombre in ap.aprendizajes_para_prompt() and "eliminatorio" in ap.aprendizajes_para_prompt().lower()

@pytest.mark.asyncio
async def test_aprendizaje_equivalente_llega_al_estratega(tmp_path,monkeypatch):
    monkeypatch.setattr(ap,"APRENDIZAJES_FILE",tmp_path/"aprendizajes.json")
    ap.registrar_aprendizaje("Experiencia en rubro gastronómico","BRECHA_REAL",eliminatorio=True)
    from core import strategist
    recibido=[]
    class Bridge:
        effort="low"
        async def run_prompt(self,prompt,**kw):
            recibido.append(prompt)
            assert "Experiencia en rubro gastronómico" in prompt
            assert "eliminatorio" in prompt.lower() and "true" in prompt.lower()
            return {"requisitos":[{"nombre":"Experiencia en gastronomía","tipo":"REQUISITO_EXCLUYENTE",
                    "estado":"BRECHA_REAL","eliminatorio":True,"categoria_descarte":"RUBRO"}]}
    class Expander:
        def get_expanded_competencies(self): return []
    agente=strategist.StrategistAgent(bridge=Bridge(),expander=Expander())
    monkeypatch.setattr(agente,"load_candidate_experience",lambda:"Soporte IT")
    r=await agente.analyze_offer("Experiencia en gastronomía exigida")
    assert recibido and r["requisitos"][0]["eliminatorio"] is True
