"""Reglas del Radar en el perfil: `<DATA_DIR>/candidato/reglas_radar.json`, sin valores en el código."""
import copy
import json

import pytest

import core.paths
from core import reglas_radar
from core.reglas_radar import ReglasInvalidas, leer_reglas, validar_reglas

VALIDAS = {
    "busquedas_por_hora": 6,
    "busquedas_por_dia": 60,
    "porcentaje_si_las_dos_cuentas_se_pausaron": 67,
    "pausa_entre_lecturas_min": {"desde": 3, "hasta": 6},
    "pausa_entre_vueltas_min": {"desde": 25, "hasta": 35},
    "lectura_busqueda": {"posts": 300, "scrolls": 15, "pantallas_sin_nuevos": 2},
    "lectura_feed": {"posts": 80, "scrolls": 20},
    "pausa_bloqueo_horas": {"primera": 6, "maxima": 24},
    "tope_envios_dia": 40,
    "cuenta_feed": "principal",
    "cuenta_busquedas": "secundaria",
}


@pytest.fixture
def archivo(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path)
    (tmp_path / "candidato").mkdir()

    def escribir(contenido):
        texto = contenido if isinstance(contenido, str) else json.dumps(contenido)
        (tmp_path / "candidato" / reglas_radar.ARCHIVO).write_text(texto, encoding="utf-8")
    return escribir


def _con(**cambios):
    datos = copy.deepcopy(VALIDAS)
    for clave, valor in cambios.items():
        if valor is None:
            datos.pop(clave)
        else:
            datos[clave] = valor
    return datos


def test_archivo_valido(archivo):
    archivo(VALIDAS)
    r = leer_reglas()
    assert (r.busquedas_por_hora, r.busquedas_por_dia, r.porcentaje_dos_pausadas) == (6, 60, 67)
    assert r.pausa_entre_lecturas_s == (180.0, 360.0) and r.pausa_entre_vueltas_s == (1500.0, 2100.0)
    assert (r.busqueda_posts, r.busqueda_scrolls, r.busqueda_pantallas_sin_nuevos) == (300, 15, 2)
    assert (r.feed_posts, r.feed_scrolls) == (80, 20)
    assert (r.bloqueo_primera_h, r.bloqueo_maxima_h) == (6, 24)
    assert r.tope_envios_dia == 40 and not hasattr(r, "tope_posts_dia")  # sin tope de posts
    assert validar_reglas(VALIDAS) == []


def test_presupuesto_con_las_dos_cuentas_pausadas(archivo):
    archivo(VALIDAS)
    r = leer_reglas()
    assert r.presupuesto(dos_pausadas=False) == (6, 60)
    assert r.presupuesto(dos_pausadas=True) == (4, 40)  # 67 %, redondeado hacia abajo: lo de antes (2/3)


def test_sin_archivo_o_roto(archivo):
    with pytest.raises(ReglasInvalidas, match="No existe"):
        leer_reglas()
    archivo("{roto")
    with pytest.raises(ReglasInvalidas, match="JSON"):
        leer_reglas()
    archivo([1, 2])
    with pytest.raises(ReglasInvalidas, match="objeto"):
        leer_reglas()


@pytest.mark.parametrize("cambios, fragmento", [
    ({"busquedas_por_hora": None}, "Falta «busquedas_por_hora»"),
    ({"tope_envios_dia": None}, "Falta «tope_envios_dia»"),
    ({"busquedas_por_hora": 0}, "«busquedas_por_hora» tiene que ser un entero mayor que 0"),
    ({"busquedas_por_dia": "60"}, "«busquedas_por_dia» tiene que ser un entero mayor que 0"),
    ({"tope_envios_dia": True}, "«tope_envios_dia» tiene que ser un entero mayor que 0"),
    ({"tope_envios_dia": 1.5}, "«tope_envios_dia» tiene que ser un entero mayor que 0"),
    ({"tope_posts_dia": 600}, "Campo desconocido: «tope_posts_dia»"),
    ({"cuenta_busquedas": "otra"}, "«cuenta_busquedas» tiene que ser «principal» o «secundaria»"),
    ({"cuenta_feed": 1}, "«cuenta_feed» tiene que ser «principal» o «secundaria»"),
    ({"porcentaje_si_las_dos_cuentas_se_pausaron": 101}, "entre 1 y 100"),
    ({"busquedas_por_dia": 5}, "no pueden ser menos que las búsquedas por hora"),
    ({"pausa_entre_lecturas_min": {"desde": 7, "hasta": 6}}, "«desde» no puede ser mayor que «hasta»"),
    ({"pausa_entre_vueltas_min": {"desde": 25}}, "Falta «pausa_entre_vueltas_min.hasta»"),
    ({"pausa_entre_vueltas_min": 30}, "«pausa_entre_vueltas_min» tiene que ser un objeto"),
    ({"lectura_busqueda": {"posts": 300, "scrolls": 15}}, "Falta «lectura_busqueda.pantallas_sin_nuevos»"),
    ({"lectura_feed": {"posts": 80, "scrolls": 0}}, "«lectura_feed.scrolls» tiene que ser un entero mayor que 0"),
    ({"pausa_bloqueo_horas": {"primera": 30, "maxima": 24}}, "«primera» no puede ser mayor que «maxima»"),
    ({"extra": 1}, "Campo desconocido: «extra»"),
    ({"lectura_feed": {"posts": 80, "scrolls": 20, "x": 1}}, "Campo desconocido: «lectura_feed.x»"),
])
def test_cada_error_dice_que_falta_o_esta_mal(archivo, cambios, fragmento):
    datos = _con(**cambios)
    errores = validar_reglas(datos)
    assert any(fragmento in e for e in errores), errores
    archivo(datos)
    with pytest.raises(ReglasInvalidas) as exc:
        leer_reglas()
    assert fragmento in str(exc.value)


def test_el_codigo_no_tiene_valores_propios():
    """Los únicos números de las reglas están en el perfil: el módulo no trae valores por defecto."""
    from pathlib import Path
    fuente = (Path(reglas_radar.__file__)).read_text(encoding="utf-8")
    for valor in ("600", "40", "300", "80", "60", "67", "25", "35", "24"):
        assert f"= {valor}" not in fuente and f": {valor}" not in fuente


def test_las_cuentas_se_leen_y_pueden_faltar(archivo):
    """feed con la principal y búsquedas con la secundaria, desde el perfil. Si falta una, la
    lectura que la usa no se hace (el Radar lo decide); el resto de las reglas sigue valiendo."""
    archivo(VALIDAS)
    r = leer_reglas()
    assert (r.cuenta_feed, r.cuenta_busquedas) == ("principal", "secundaria")
    archivo(_con(cuenta_feed=None, cuenta_busquedas=None))
    r = leer_reglas()
    assert (r.cuenta_feed, r.cuenta_busquedas) == (None, None) and r.busquedas_por_hora == 6
