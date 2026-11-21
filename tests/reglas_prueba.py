"""Reglas del Radar para tests: las de `tests/fixtures/candidato/reglas_radar.json` (los valores reales de hoy)."""
import dataclasses
import json
from pathlib import Path

from core.reglas_radar import Reglas

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "candidato" / "reglas_radar.json"
DATOS_REALES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _desde_datos(d) -> Reglas:
    return Reglas(
        busquedas_por_hora=d["busquedas_por_hora"], busquedas_por_dia=d["busquedas_por_dia"],
        porcentaje_dos_pausadas=d["porcentaje_si_las_dos_cuentas_se_pausaron"],
        pausa_entre_lecturas_s=(d["pausa_entre_lecturas_min"]["desde"] * 60.0,
                                d["pausa_entre_lecturas_min"]["hasta"] * 60.0),
        pausa_entre_vueltas_s=(d["pausa_entre_vueltas_min"]["desde"] * 60.0,
                               d["pausa_entre_vueltas_min"]["hasta"] * 60.0),
        busqueda_posts=d["lectura_busqueda"]["posts"], busqueda_scrolls=d["lectura_busqueda"]["scrolls"],
        busqueda_pantallas_sin_nuevos=d["lectura_busqueda"]["pantallas_sin_nuevos"],
        feed_posts=d["lectura_feed"]["posts"], feed_scrolls=d["lectura_feed"]["scrolls"],
        bloqueo_primera_h=d["pausa_bloqueo_horas"]["primera"], bloqueo_maxima_h=d["pausa_bloqueo_horas"]["maxima"],
        tope_envios_dia=d["tope_envios_dia"],
        cuenta_feed=d.get("cuenta_feed"), cuenta_busquedas=d.get("cuenta_busquedas"),
    )


REALES = _desde_datos(DATOS_REALES)


def reglas(**cambios) -> Reglas:
    """Las reales con `cambios` (nombres de campo de `Reglas`)."""
    return dataclasses.replace(REALES, **cambios)


def usar(monkeypatch, valor) -> None:
    """El Radar usa `valor` (Reglas, o una excepción para simular reglas inválidas) en vez de leer el perfil."""
    import core.radar

    def _leer():
        if isinstance(valor, BaseException):
            raise valor
        return valor
    monkeypatch.setattr(core.radar, "leer_reglas", _leer)
