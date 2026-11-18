"""Afinidad honesta (creador, 08/11): los requisitos genéricos pesan poco, el modelo no se aprueba solo los
transferibles y el grupo de rol sale del análisis."""
import pytest

from core import strategist
from core.prioridades import grupo_rol, prioridad_rol


def req(nombre, estado, tipo="RESPONSABILIDAD", approved=None):
    r = {"nombre": nombre, "estado": estado, "tipo": tipo}
    if approved is not None:
        r["approved"] = approved
    return r


def test_genericos_no_inflan_un_rol_ajeno():
    # Caso «Administrativo/a»: el núcleo del rol es brecha y lo genérico es match
    reqs = [req("Conciliación de pagos", "BRECHA_REAL"), req("Control de cobranzas", "BRECHA_REAL"),
            req("Manejo de Excel", "MATCH_DIRECTO", "REQUISITO_EXCLUYENTE"),
            req("Paquete Office", "MATCH_DIRECTO", "REQUISITO_EXCLUYENTE"),
            req("Perfil organizado y responsable", "MATCH_DIRECTO"), req("Jornada completa", "MATCH_DIRECTO"),
            req("Modalidad presencial en CABA", "MATCH_DIRECTO")]
    score, _ = strategist.calculate_match_score_with_breakdown(reqs)
    assert score < 50  # antes: 5/7 = 71,4


def test_peso_por_tipo():
    assert strategist.peso_requisito(req("Inglés técnico", "MATCH_DIRECTO")) == 0.25
    assert strategist.peso_requisito(req("Docker", "MATCH_DIRECTO", "REQUISITO_DESEABLE")) == 0.5
    assert strategist.peso_requisito(req("Relevamiento funcional", "MATCH_DIRECTO")) == 1.0
    todos = [req("A", "MATCH_DIRECTO"), req("Excel", "MATCH_DIRECTO")]
    assert strategist.calculate_match_score(todos) == 100.0


def test_transferible_desaprobado_vale_parcial():
    t = [req("Supervisión de equipos", "TRANSFERIBLE", approved=False)]
    assert strategist.calculate_match_score(t) == 40.0
    t[0]["approved"] = True
    assert strategist.calculate_match_score(t) == 70.0


@pytest.mark.asyncio
@pytest.mark.parametrize("approved_modelo", [True, False, None])
async def test_transferible_del_modelo_cuenta_solo_y_entra_al_cv(monkeypatch, approved_modelo):
    """Creador, 08/11: los transferibles no necesitan su aprobación (si no, hay que regenerar CV y mail)."""
    trans = {"nombre": "Supervisión de equipos de soporte", "estado": "TRANSFERIBLE", "tipo": "RESPONSABILIDAD"}
    if approved_modelo is not None:
        trans["approved"] = approved_modelo

    class Bridge:
        effort = "low"

        async def run_prompt(self, prompt, **kw):
            assert '"grupo_rol"' in prompt and "Sé estricto con la función" in prompt
            return {"puesto": "X", "grupo_rol": "A", "requisitos": [
                dict(trans), {"nombre": "SQL", "estado": "MATCH_DIRECTO", "approved": True, "tipo": "REQUISITO_EXCLUYENTE"}]}

    monkeypatch.setattr(strategist, "aplicar_aprendizajes_a_requisitos", lambda tags: 0)
    agente = strategist.StrategistAgent(bridge=Bridge())
    monkeypatch.setattr(agente.expander, "get_expanded_competencies", lambda: [])
    r = await agente.analyze_offer("Oferta")
    assert r["requisitos"][0]["approved"] is True
    assert r["match_score"] == 85.0  # (0,70 + 1) / 2
    # El writer destaca los requisitos con approved (core/writer.py): el transferible entra al CV
    destacados = [q["nombre"] for q in r["requisitos"] if q.get("approved", q.get("estado") == "MATCH_DIRECTO")]
    assert "Supervisión de equipos de soporte" in destacados


def test_genericos_siguen_sin_inflar_con_transferibles_aprobados():
    reqs = [req("Conciliación de pagos", "BRECHA_REAL"), req("Control de cobranzas", "BRECHA_REAL"),
            req("Atención al público", "TRANSFERIBLE", approved=True),
            req("Manejo de Excel", "MATCH_DIRECTO"), req("Perfil organizado", "MATCH_DIRECTO"),
            req("Jornada completa", "MATCH_DIRECTO")]
    assert strategist.calculate_match_score(reqs) < 70


@pytest.mark.parametrize("puesto,grupo", [
    ("Content & Operations Manager", "c"), ("Growth Operations Specialist", "c"), ("Operations", "a"),
    ("IT Operations & Security Lead", "a"), ("Business Technical Analyst Ssr", "a"),
    ("Especialista en SOC", "b"), ("Founding Engineer", "b"), ("Product Owner - Open Text", "b"),
    ("Security Guard", "c"), ("Civil Engineer", "c"),
])
def test_grupos_por_titulo(puesto, grupo):
    assert prioridad_rol(puesto) == grupo


def test_grupo_del_analisis_manda():
    post = {"triage": {"puesto": "Desarrollador DAM"}, "analisis": {"puesto": "Desarrollador DAM", "grupo_rol": "C"}}
    assert grupo_rol(post) == "c"
    post["analisis"]["grupo_rol"] = "raro"
    assert grupo_rol(post) == "b"
