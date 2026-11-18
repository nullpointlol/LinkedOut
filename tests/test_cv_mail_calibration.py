"""Controles sintéticos del cálculo; no acreditan CA4 (corpus Q3 pendiente)."""
import copy
import importlib
import importlib.util
import io
import json
import os
import sqlite3

import pytest

from core.auditor import scan_anti_ai_writing
from core.bridge import AntigravityBridge


SENALES = ("impacto vago", "repetición cercana", "simetría sintáctica", "raya")
VAGO = "Ordené tickets para acelerar el despacho según SLA."
REPETIDA = "Cambio de guardia. Pase de guardia. Cambio de guardia."
SIMETRICA = ("• Automaticé reportes, reduciendo tiempos de auditoría.\n"
             "• Validé reglas, evitando fallas en producción.")


def casos_sinteticos():
    # Etiquetas humanas simuladas explícitas: una detección sola no implica FP.
    return [
        dict(id="s01", cv=VAGO, subject="SQL — API", body=REPETIDA,
             falsas_positivas=["impacto vago", "raya"]),
        dict(id="s02", cv=SIMETRICA, subject="QA", body=VAGO,
             falsas_positivas=["simetría sintáctica"]),
        dict(id="s03", cv=REPETIDA, subject="API – SQL", body=REPETIDA,
             falsas_positivas=["repetición cercana"]),
        dict(id="s04", cv="Consulté SQL.", subject="QA", body="Validé APIs REST.", falsas_positivas=[]),
        dict(id="s05", cv="Probé SOAP.", subject="Soporte", body="Escribí scripts Python.", falsas_positivas=[]),
        dict(id="s06", cv="Revisé logs.", subject="Datos", body="Configuré índices SQL.", falsas_positivas=[]),
        dict(id="s07", cv="Documenté endpoints.", subject="API", body="Probé HTTP 200.", falsas_positivas=[]),
        dict(id="s08", cv="Medí latencia.", subject="Redes", body="Registré errores TCP.", falsas_positivas=[]),
        dict(id="s09", cv="Armé pruebas unitarias.", subject="Python", body="Usé pytest.", falsas_positivas=[]),
        dict(id="s10", cv="Validé JSON.", subject="Backend", body="Consulté PostgreSQL.", falsas_positivas=[]),
    ]


def api():
    # Fallo explícito por API faltante, sin romper colección ni modo invariantes.
    assert importlib.util.find_spec("core.quality_calibration") is not None, "Falta la API de calibración"
    return importlib.import_module("core.quality_calibration").calibrar_senales


def test_fp_por_postulacion_y_senal():
    # invariante: tareas técnicas aisladas siguen limpias en el scanner actual.
    assert scan_anti_ai_writing("Consulté SQL para consultar datos.")["clean"]
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    calibrar = api()
    casos = casos_sinteticos()
    originales = copy.deepcopy(casos)
    reporte = calibrar(casos)
    assert reporte == {"n": 10, "por_senal": {
        "impacto vago": {"detectadas": 2, "falsas_positivas": 1, "tasa_fp": 0.1},
        "repetición cercana": {"detectadas": 2, "falsas_positivas": 1, "tasa_fp": 0.1},
        "simetría sintáctica": {"detectadas": 1, "falsas_positivas": 1, "tasa_fp": 0.1},
        "raya": {"detectadas": 2, "falsas_positivas": 1, "tasa_fp": 0.1},
    }, "cumple": True}
    assert casos == originales
    assert calibrar(list(reversed(casos))) == reporte
    assert all(campo not in json.dumps(reporte, ensure_ascii=False)
               for campo in ("s01", VAGO, "cv", "subject", "body"))
    # No fabricar repetición ni simetría concatenando documentos distintos.
    separados = casos_sinteticos()
    separados[0].update(cv="Cambio de guardia.", subject="Pase de guardia.",
                         body="", falsas_positivas=[])
    separados[1].update(cv=SIMETRICA.splitlines()[0], subject="QA",
                         body=SIMETRICA.splitlines()[1], falsas_positivas=[])
    assert calibrar(separados)["por_senal"]["repetición cercana"]["detectadas"] == 1
    assert calibrar(separados)["por_senal"]["simetría sintáctica"]["detectadas"] == 0
    sin_etiquetas = casos_sinteticos()
    for caso in sin_etiquetas:
        caso["falsas_positivas"] = []
        caso["status"] = "LISTO"
    assert all(s["falsas_positivas"] == 0 for s in calibrar(sin_etiquetas)["por_senal"].values())
    del sin_etiquetas[0]["falsas_positivas"]
    with pytest.raises(ValueError):
        calibrar(sin_etiquetas)


@pytest.mark.parametrize("senal", SENALES)
def test_dos_pasa_tres_falla(senal):
    # invariante: un gerundio técnico aislado no dispara simetría.
    assert not any(d.startswith("simetría sintáctica") for d in
                   scan_anti_ai_writing("Consulté SQL usando índices.")["detected_words"])
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    calibrar = api()
    # Separar el párrafo final: sin línea vacía continúa la última viñeta PDF.
    texto = VAGO + "\n" + REPETIDA + "\n" + SIMETRICA + "\n\nSQL — API"
    assert any(d.startswith(senal + " (") for d in scan_anti_ai_writing(texto)["detected_words"])
    casos = casos_sinteticos()
    for i, caso in enumerate(casos):
        caso.update(cv=texto, subject=texto, body=texto,
                    falsas_positivas=[senal, senal] if i < 2 else [])
    reporte = calibrar(casos)
    assert reporte["cumple"] is True
    assert reporte["por_senal"][senal] == {"detectadas": 10, "falsas_positivas": 2, "tasa_fp": 0.2}
    casos[2]["falsas_positivas"] = [senal]
    reporte = calibrar(casos)
    assert reporte["cumple"] is False
    assert reporte["por_senal"][senal] == {"detectadas": 10, "falsas_positivas": 3, "tasa_fp": 0.3}


def test_exige_diez_distintos_y_no_hay_io(monkeypatch):
    def prohibido(*args, **kwargs):
        pytest.fail("La calibración no puede hacer I/O ni llamadas LLM")

    # invariante: scanner puro existente, sin abrir archivos/SQLite ni usar LLM.
    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", prohibido)
        guard.setattr(io, "open", prohibido)
        guard.setattr(os, "open", prohibido)
        guard.setattr(sqlite3, "connect", prohibido)
        guard.setattr(AntigravityBridge, "run_prompt", prohibido)
        assert scan_anti_ai_writing("Validé SQL.")["clean"]
        if os.environ.get("INVARIANT_CHECK") == "1":
            return
        calibrar = api()
        casos = casos_sinteticos()
        assert calibrar(casos) == calibrar(copy.deepcopy(casos))
        for invalidos in (casos[:9], casos + [dict(casos[0], id="s11")],
                          casos[:9] + [casos[0]]):
            with pytest.raises(ValueError):
                calibrar(invalidos)
        for campo, valor in (("id", 1), ("cv", None), ("subject", 1),
                             ("body", []), ("falsas_positivas", None),
                             ("falsas_positivas", ["desconocida"])):
            invalidos = copy.deepcopy(casos)
            invalidos[0][campo] = valor
            with pytest.raises(ValueError):
                calibrar(invalidos)
