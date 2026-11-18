"""Bordes del motor: reglas de afinidad, hilo del Radar, reglas de portales y calibración. Sin red ni datos reales."""
import asyncio
import sqlite3

import pytest

from core import afinidad_reglas as ar
from core import quality_calibration as qc
from core import reglas_portales as rp
from core.radar_hilo import RadarEnHilo
from tests.portales_prueba import regla_dato


# ------------------------------------------------------------------ afinidad

def test_afinidad_entradas_raras():
    assert ar._extraer_anios("") is None
    assert ar._areas_tematicas(["no es área", {"area": "Total de experiencia"}, {"area": "Monitoreo"}]) == [
        {"area": "Monitoreo"}]
    assert ar.aplicar_reglas("no es lista", "", [], "") == []
    assert ar.aplicar_reglas([3], "Analista", [], ["base"]) == [3]


def test_base_del_candidato_con_archivos_ilegibles(tmp_path):
    (tmp_path / "experiencia.txt").mkdir()  # existe pero no se puede leer como texto
    (tmp_path / "competencias_expandidas.json").write_text("{roto", encoding="utf-8")
    assert ar.cargar_base_candidato(tmp_path) == ""


# ------------------------------------------------------------------ hilo del Radar

class _Radar:
    def __init__(self):
        self.cerrado = 0

    async def stop_for_shutdown(self):
        self.cerrado += 1

    async def colgar(self):
        asyncio.get_running_loop().create_task(asyncio.sleep(3600))  # queda pendiente al cerrar
        return "ok"


def test_hilo_del_radar_cancela_lo_pendiente_al_cerrar():
    radar = _Radar()
    hilo = RadarEnHilo(radar, nombre="prueba")
    assert hilo.radar is radar
    hilo.cerrar_ya()  # sin hilo, no hace nada
    assert asyncio.run(hilo.colgar()) == "ok"
    assert hilo.hilo_vivo and hilo._arrancar() is hilo.loop  # ya andando: el mismo loop
    hilo.cerrar_ya()
    assert radar.cerrado == 1 and not hilo.hilo_vivo


# ------------------------------------------------------------------ reglas de portales

@pytest.mark.parametrize("cambio,error", [
    ({"activo": None}, "falta «activo»"),
    ({"modo": "otro"}, "«modo» tiene que ser todas o busquedas"),
])
def test_reglas_de_portal_invalidas(cambio, error):
    dato = regla_dato("indeed")
    for clave, valor in cambio.items():
        if valor is None:
            dato.pop(clave)
        else:
            dato[clave] = valor
    assert any(error in e for e in rp.errores_de_portal("indeed", dato))


def test_reglas_de_duplicados_invalidas():
    assert rp.errores_de_duplicados([]) == ["«duplicados» tiene que ser un objeto con umbral_titulo y dias."]
    errores = rp.errores_de_duplicados({"umbral_titulo": 150, "dias": 0})
    assert any("umbral_titulo" in e for e in errores) and any("«dias»" in e for e in errores)


# ------------------------------------------------------------------ calibración de señales

def _casos(**cambio):
    casos = [{"id": str(i), "cv": "", "subject": "", "body": "", "falsas_positivas": []} for i in range(10)]
    casos[0].update(cambio)
    return casos


def test_calibracion_rechaza_un_pitch_que_no_es_texto():
    with pytest.raises(ValueError, match="pitch"):
        qc.calibrar_senales(_casos(pitch=3))


def _copia(tmp_path, filas):
    db = tmp_path / "copia" / "postulaciones.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE postulaciones (id INTEGER PRIMARY KEY, cv_markdown TEXT, pitch TEXT)")
        c.execute("CREATE TABLE linkedin_posts (id INTEGER PRIMARY KEY, postulacion_id INT, estado TEXT, "
                  "email_subject TEXT, email_body TEXT, created_at TEXT)")
        for i in range(filas):
            c.execute("INSERT INTO postulaciones VALUES (?, 'cv', '')", (i,))
            c.execute("INSERT INTO linkedin_posts VALUES (?, ?, 'LISTO', 'asunto', 'cuerpo', '2026-11-17')", (i, i))
    return db


def test_calibracion_rechaza_la_base_real_pocas_filas_y_etiquetas_malas(tmp_path):
    real = tmp_path / "otra" / "data" / "postulaciones.db"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"")
    with pytest.raises(SystemExit):
        qc.main([str(real)])
    with pytest.raises(SystemExit):
        qc.main([str(_copia(tmp_path, 3))])
    etiquetas = tmp_path / "etiquetas.json"
    etiquetas.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SystemExit):
        qc.main([str(tmp_path / "copia" / "postulaciones.db"), "--etiquetas", str(etiquetas)])


# ------------------------------------------------------------------ perfil editable

def test_perfil_rechaza_contenido_que_no_es_texto_ni_utf8(tmp_path):
    from core import perfil_candidato as pc
    with pytest.raises(pc.PerfilError, match="tiene que ser texto"):
        pc._validar("experiencia", 3, tmp_path)
    with pytest.raises(pc.PerfilError, match="UTF-8"):
        pc._validar("experiencia", "texto con \ud800 suelto", tmp_path)


# ------------------------------------------------------------------ descartes y guardas del CV

@pytest.mark.parametrize("restricciones", [
    {"residencia": ["", "CABA"]}, {"edad": {"actual": 40, "al": 20261117}},
])
def test_restricciones_invalidas(restricciones):
    from core import descartes
    assert descartes._validas(restricciones) is False


def test_edad_menor_de_y_requisitos_raros():
    from core import descartes
    assert descartes._limites_edad("edad: menores de 35") == (None, 34)
    validas = {"base": "Ciudad de Buenos Aires"}
    assert descartes.motivos_descarte({"requisitos": "no es lista"}, validas) == []
    assert descartes.motivos_descarte({"requisitos": ["texto", {"nombre": 3}]}, validas) == []


def test_guardas_del_cv_bordes():
    from core import cv_guardas as g
    assert g.es_conocimiento_ausente("", ["Kubernetes"]) is False
    assert g.es_conocimiento_ausente("Uso Kubernetes", ["", "kubernetes"]) is True
    areas = [{"area": "Monitoreo", "anios": "3"}, {"area": "Soporte", "anios": "5 años"}]
    assert g._total(areas)["area"] == "Soporte" and g._total([]) is None
    assert g.corregir_anios("con 5 años de experiencia", [])[0] == "con 5 años de experiencia"


def test_lecturas_del_perfil_que_fallan(tmp_path, monkeypatch):
    from core import perfil_areas, profile_parser, titulo_cv
    monkeypatch.setattr(profile_parser, "DEFAULT_CANDIDATO_DIR", tmp_path / "no-existe")
    assert titulo_cv._leer("personales.txt") == ""
    monkeypatch.setattr(perfil_areas, "_leer_perfil", lambda d: (_ for _ in ()).throw(OSError("bloqueado")))
    assert perfil_areas.anios_por_area_en_cache(tmp_path) == []


def test_regla_de_portal_sin_modo():
    dato = regla_dato("indeed")
    dato.pop("modo")
    assert any("falta «modo»" in e for e in rp.errores_de_portal("indeed", dato))
