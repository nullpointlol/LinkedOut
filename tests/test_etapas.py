"""Métricas por etapa: cada etapa del pipeline guarda segundos, llamadas a agy, modelo y tokens."""
import pytest

from core.bridge import AntigravityBridge, etapa, resumen_etapas


def _bridge(telemetria=None):
    b = AntigravityBridge.__new__(AntigravityBridge)
    b.model, b.effort, b.last_telemetry = "gemini-3.8-flash-high", "high", telemetria
    return b


def test_stage_counts_agy_calls_model_effort_and_tokens():
    b = _bridge({"total_tokens": 1200})
    with etapa("auditoria") as registro:
        b._anotar_llamada("low")
        b._anotar_llamada("low")
    assert registro["etapa"] == "auditoria" and registro["llamadas"] == 2
    assert registro["modelo"] == "gemini-3.8-flash-low" and registro["effort"] == "low"
    assert registro["tokens"] == 2400 and registro["segundos"] >= 0 and registro["error"] is None


def test_stage_keeps_timeout_and_failure():
    b = _bridge()
    with pytest.raises(TimeoutError):
        with etapa("redactor") as registro:
            b._anotar_llamada("high", error="timeout 600s")
            raise TimeoutError("La llamada a Antigravity CLI excedió el tiempo límite de 600s.")
    assert registro["llamadas"] == 1 and "600s" in registro["error"]


def test_calls_outside_a_stage_are_not_recorded():
    _bridge({"total_tokens": 5})._anotar_llamada("low")  # no falla sin etapa activa


def test_summary_adds_the_total():
    r = resumen_etapas([{"etapa": "redactor", "segundos": 160.2}, {"etapa": "auditoria", "segundos": 9.1}])
    assert r["total_s"] == 169.3 and [e["etapa"] for e in r["etapas"]] == ["redactor", "auditoria"]


@pytest.mark.asyncio
async def test_radar_compose_adds_the_mail_stage_to_the_postulacion(monkeypatch):
    import server
    guardado = {}
    postulacion = {"id": 7, "status": "READY", "cv_markdown": "# CV",
                   "telemetry": {"model": "m", "etapas": [{"etapa": "redactor", "segundos": 100.0}], "total_s": 100.0}}
    monkeypatch.setattr(server, "get_postulacion", lambda pid: postulacion)
    monkeypatch.setattr(server, "update_postulacion", lambda pid, **kw: guardado.update(kw))

    async def fake_compose(post, post_ulacion):
        return {"to": "a@b.test", "subject": "S", "body": "B"}

    monkeypatch.setattr(server, "compose_message", fake_compose)
    assert (await server._radar_compose({"id": 1}, 7))["body"] == "B"
    etapas = guardado["telemetry"]["etapas"]
    assert [e["etapa"] for e in etapas] == ["redactor", "mail"]
    assert guardado["telemetry"]["model"] == "m" and guardado["telemetry"]["total_s"] >= 100.0
