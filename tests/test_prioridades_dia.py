"""El orden de análisis agrupa por DÍA DE CALENDARIO de publicación, no por ventanas de 24 h contadas desde ahora.

Antes, `clave_analisis` usaba `int(edad_dias)`: dos posts del mismo día (08:00 y 09:00) caían en «días» distintos
según la hora en que corría, y el orden cambiaba con el reloj (test_ordenes_y_empates rojo el 12/11 a la mañana).
"""
from datetime import datetime, time as dtime, timedelta, timezone

from core.prioridades import clave_analisis


def _post(pid, local: datetime, emails, puesto="Analista Funcional"):
    # created_at se guarda en UTC sin zona, como lo escribe la base
    utc = local.astimezone(timezone.utc).replace(tzinfo=None)
    return {"id": pid, "created_at": utc.strftime("%Y-%m-%d %H:%M:%S"), "emails": emails,
            "triage": {"puesto": puesto}}


def test_mismo_dia_de_calendario_ordena_por_email_aunque_las_horas_esten_a_casi_24_h():
    dia = (datetime.now() - timedelta(days=4)).date()
    temprano_con_email = _post(1, datetime.combine(dia, dtime(0, 5)).astimezone(), ["rrhh@acme.test"])
    tarde_sin_email = _post(2, datetime.combine(dia, dtime(23, 55)).astimezone(), [])
    orden = sorted([tarde_sin_email, temprano_con_email], key=clave_analisis)
    assert [p["id"] for p in orden] == [1, 2]


def test_el_dia_mas_reciente_va_primero_aunque_no_tenga_email():
    hoy = datetime.now().date()
    ayer_con_email = _post(1, datetime.combine(hoy - timedelta(days=1), dtime(12, 0)).astimezone(), ["rrhh@acme.test"])
    hoy_sin_email = _post(2, datetime.combine(hoy, dtime(0, 1)).astimezone(), [])
    assert [p["id"] for p in sorted([ayer_con_email, hoy_sin_email], key=clave_analisis)] == [2, 1]


def test_sin_fecha_va_al_final():
    con_fecha = _post(1, datetime.now().astimezone() - timedelta(days=30), [])
    sin_fecha = {"id": 2, "created_at": "", "emails": ["rrhh@acme.test"], "triage": {"puesto": "Analista Funcional"}}
    assert [p["id"] for p in sorted([sin_fecha, con_fecha], key=clave_analisis)] == [1, 2]
