"""Formato de `busquedas.json` (11/11): búsquedas 100 % del usuario, armadas y validadas en el código."""
import json

import pytest

import core.paths
from core import radar_consultas as rc
from core.radar_consultas import armar_consulta, validar_busquedas, validar_consulta


def t(texto, **extra):
    return {"texto": texto, **extra}


def busqueda(**extra):
    base = {"nombre": "Prueba", "activa": True}
    base.update(extra)
    return base


# ------------------------------------------------------------------ armado

def test_consulta_libre_va_tal_cual():
    assert armar_consulta(busqueda(consulta='  "cv a"  ')) == '"cv a"'


def test_bloques_or_adentro_y_espacio_entre_bloques():
    b = busqueda(bloques=[
        [t("cv a", exacta=True), t("enviar cv", exacta=True)],
        [t("funcional"), t("business analyst", exacta=True)],
    ])
    assert armar_consulta(b) == '("cv a" OR "enviar cv") (funcional OR "business analyst")'


def test_un_termino_solo_no_lleva_parentesis():
    b = busqueda(bloques=[[t("cv a", exacta=True)], [t("Argentina")]])
    assert armar_consulta(b) == '"cv a" Argentina'


def test_varias_palabras_sin_exacta():
    solo = busqueda(bloques=[[t("soporte aplicaciones")]])
    assert armar_consulta(solo) == "soporte aplicaciones"
    en_or = busqueda(bloques=[[t("soporte aplicaciones"), t("helpdesk")]])
    assert armar_consulta(en_or) == "((soporte AND aplicaciones) OR helpdesk)"


def test_excluir_va_con_not():
    b = busqueda(bloques=[
        [t("cv a", exacta=True), t("pasantía", excluir=True)],
        [t("senior", excluir=True), t("trainee", excluir=True, exacta=True)],
    ])
    assert armar_consulta(b) == '"cv a" NOT pasantía NOT senior NOT "trainee"'


def test_espacios_de_sobra_se_limpian():
    b = busqueda(bloques=[[t("  mesa   ayuda ", exacta=True)]])
    assert armar_consulta(b) == '"mesa ayuda"'


# ------------------------------------------------------------------ validación de una consulta

@pytest.mark.parametrize("consulta", [
    '"cv a"',
    '("cv a" OR "enviar cv") (funcional OR "business analyst")',
    '"cv a" NOT pasantía',
    '"night shift" (Argentina OR LATAM)',
    '"cv a or b"',  # dentro de comillas las minúsculas son texto
])
def test_consultas_validas(consulta):
    assert validar_consulta(consulta) == []


@pytest.mark.parametrize("consulta,parte", [
    ("", "vacía"),
    ('"cv a', "comillas"),
    ("“cv a”", "comillas rectas"),
    ("«cv a»", "comillas rectas"),
    ("(cv OR mail", "paréntesis"),
    ("cv OR mail)", "paréntesis"),
    ("cv ()", "paréntesis vacíos"),
    ("cv or mail", "mayúsculas"),
    ("cv And mail", "mayúsculas"),
    ("cv not mail", "mayúsculas"),
    ("analist*", "*"),
    ("cv [a]", "corchetes"),
    ("OR cv", "operador"),
    ("cv OR", "operador"),
    ("cv OR AND mail", "operador"),
    ("(OR cv)", "operador"),
    ("cv NOT", "operador"),
])
def test_consultas_invalidas(consulta, parte):
    errores = validar_consulta(consulta)
    assert errores and any(parte in e for e in errores), errores


def test_largo_maximo():
    assert validar_consulta("a" * rc.LARGO_MAXIMO) == []
    errores = validar_consulta("a" * (rc.LARGO_MAXIMO + 1))
    assert errores and str(rc.LARGO_MAXIMO) in errores[0]


# ------------------------------------------------------------------ validación del archivo

def test_archivo_valido_sin_errores():
    datos = {"busquedas": [
        busqueda(nombre="Uno", consulta='"cv a"'),
        busqueda(nombre="Dos", activa=False,
                 bloques=[[t("send your cv", exacta=True)], [t("LATAM")]]),
    ]}
    assert validar_busquedas(datos) == []


def test_lista_vacia_es_valida():
    assert validar_busquedas({"busquedas": []}) == []


def test_formato_viejo_se_rechaza_con_aviso():
    errores = validar_busquedas({"variantes_envio": ["cv a"], "grupos_rol": {}})
    assert len(errores) == 1 and "formato viejo" in errores[0]


@pytest.mark.parametrize("datos", [None, [], "x", {"busquedas": "x"}, {}])
def test_sin_lista_de_busquedas(datos):
    errores = validar_busquedas(datos)
    assert errores and "busquedas" in errores[0]


@pytest.mark.parametrize("cambio,parte", [
    ({"nombre": ""}, "nombre"),
    ({"nombre": 3}, "nombre"),
    ({"activa": "sí"}, "activa"),
    ({}, "bloques"),  # ni bloques ni consulta
    ({"consulta": "x", "bloques": [[t("y")]]}, "solo una"),
    ({"bloques": []}, "bloques"),
    ({"bloques": [[]]}, "bloque 1"),
    ({"bloques": [[t("")]]}, "término 1"),
    ({"bloques": [[t('con "comillas"')]]}, "término 1"),
    ({"bloques": [[t("con (paréntesis)")]]}, "término 1"),
    ({"bloques": [[t("OR")]]}, "término 1"),
    ({"bloques": [[t("x", exacta="sí")]]}, "exacta"),
    ({"bloques": [[t("x", excluir=1)]]}, "excluir"),
    ({"bloques": [[t("x", excluir=True)]]}, "excluir"),  # solo excluidos: no busca nada
    ({"consulta": "cv or mail"}, "mayúsculas"),
    ({"consulta": 5}, "consulta"),
])
def test_errores_por_busqueda(cambio, parte):
    b = {"nombre": "Prueba", "activa": True}
    b.update(cambio)
    errores = validar_busquedas({"busquedas": [b]})
    assert errores and any(parte in e for e in errores), errores


def test_errores_nombran_la_busqueda():
    errores = validar_busquedas({"busquedas": [busqueda(nombre="Análisis", consulta="cv or mail")]})
    assert errores[0].startswith("«Análisis»")
    errores = validar_busquedas({"busquedas": [{"activa": True, "consulta": "x"}]})
    assert errores[0].startswith("Búsqueda 1")


def test_nombres_repetidos():
    datos = {"busquedas": [busqueda(nombre="Uno", consulta="a"), busqueda(nombre=" uno ", consulta="b")]}
    errores = validar_busquedas(datos)
    assert any("repetido" in e for e in errores), errores


def test_largo_de_la_consulta_armada():
    largo = [t("palabra%d" % i) for i in range(60)]
    errores = validar_busquedas({"busquedas": [busqueda(bloques=[largo])]})
    assert any(str(rc.LARGO_MAXIMO) in e for e in errores), errores


# ------------------------------------------------------------------ lectura desde DATA_DIR

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core.paths, "DATA_DIR", tmp_path)
    (tmp_path / "candidato").mkdir()
    return tmp_path


def escribir(data_dir, datos):
    (data_dir / "candidato" / "busquedas.json").write_text(json.dumps(datos), encoding="utf-8")


def test_sin_archivo_no_hay_busquedas(data_dir):
    assert rc.cargar_busquedas() == []
    assert rc.consultas_activas() == []


def test_archivo_roto_no_hay_busquedas(data_dir):
    (data_dir / "candidato" / "busquedas.json").write_text("{no es json", encoding="utf-8")
    assert rc.cargar_busquedas() == []


def test_formato_viejo_no_hay_busquedas(data_dir):
    escribir(data_dir, {"variantes_envio": ["cv a"], "empresas": ["Empresa Uno"]})
    assert rc.cargar_busquedas() == []


def test_usa_las_validas_y_saltea_las_invalidas(data_dir):
    escribir(data_dir, {"busquedas": [
        busqueda(nombre="Buena", consulta='"cv a"'),
        busqueda(nombre="Mala", consulta="cv or mail"),
        busqueda(nombre="Pausada", activa=False, consulta='"cv a" mail'),
        busqueda(nombre="buena", consulta='"repetida"'),
        busqueda(nombre="Inglés", bloques=[[t("send your cv", exacta=True)], [t("LATAM")]]),
    ]})
    cargadas = rc.cargar_busquedas()
    assert [b["nombre"] for b in cargadas] == ["Buena", "Pausada", "Inglés"]
    assert cargadas[2] == {"nombre": "Inglés", "activa": True, "consulta": '"send your cv" LATAM'}
    assert rc.consultas_activas() == ['"cv a"', '"send your cv" LATAM']  # sin idioma, en el orden del archivo


def test_consultas_activas_sin_duplicados(data_dir):
    escribir(data_dir, {"busquedas": [busqueda(nombre="A", consulta='"cv a"'), busqueda(nombre="B", consulta='"cv a"')]})
    assert rc.consultas_activas() == ['"cv a"']


def test_el_ejemplo_y_la_fixture_son_validos():
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    for ruta in (raiz / "config" / "ejemplo" / "candidato" / "busquedas.json",
                 raiz / "tests" / "fixtures" / "candidato" / "busquedas.json"):
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        assert validar_busquedas(datos) == [], ruta
        assert datos["busquedas"], ruta


def test_el_codigo_no_tiene_palabras_de_busqueda_propias():
    assert not hasattr(rc, "POR_DEFECTO")
    for viejo in ("EMPRESAS", "GRUPOS_EMPRESA", "ROLES_EN_EMPRESAS", "termino_empresa", "MODIFICADORES",
                  "VARIANTES", "generar_consultas"):
        assert not hasattr(rc, viejo), viejo


# ------------------------------------------------------------------ el Radar relee el archivo

@pytest.mark.asyncio
async def test_el_radar_relee_las_busquedas_en_cada_vuelta(tmp_path, data_dir):
    """Lo que se guarda en Mi perfil rige desde la próxima vuelta, sin reiniciar el portal."""
    from core.radar import RadarLoop
    from tests.test_radar_loop import FakeHarvester, make_radar

    radar = make_radar(tmp_path, FakeHarvester())
    radar._busquedas_del_archivo = True  # como el Radar real, que no recibe `queries`
    escribir(data_dir, {"busquedas": [busqueda(nombre="Nueva", consulta='"cv a" nueva'),
                                      busqueda(nombre="EN", consulta='"send your cv"')]})
    try:
        await radar.run_cycle()
        assert '"cv a" nueva' in radar._test_created[0].calls
        assert radar._queries == ['"cv a" nueva', '"send your cv"']  # el orden del archivo, sin idioma
        assert "q1" not in radar._test_created[0].calls
    finally:
        await radar.stop()
    import inspect
    assert inspect.signature(RadarLoop).parameters["queries"].default is None


# ------------------------------------------------------------------ límites medidos en LinkedIn (docs/linkedin-sintaxis.md)

def test_hasta_cinco_or_es_valida():
    seis_frases = '("cv a" OR "enviar cv" OR "enviá tu cv" OR "cv al mail" OR "cv al correo" OR "mandá tu cv")'
    assert rc.MAX_OPERADORES == 5
    assert validar_consulta(seis_frases) == []


def test_los_and_tambien_cuentan():
    # 12/11, cuenta 2: 4 OR + 2 AND da cero; 3 OR + 1 AND trae resultados
    assert validar_consulta('("cv a" OR "enviar cv") ((soporte AND aplicaciones) OR helpdesk OR monitoreo)') == []
    errores = validar_consulta('("cv a" OR "enviar cv") ((soporte AND aplicaciones) OR (mesa AND ayuda) OR helpdesk OR monitoreo)')
    assert errores and any("6 operadores" in e for e in errores), errores


def test_el_armado_cuenta_los_and_de_los_terminos_de_varias_palabras():
    # «soporte aplicaciones» sin comillas dentro de un OR se arma como (soporte AND aplicaciones): suma un AND
    b = busqueda(bloques=[[t("cv a", exacta=True), t("enviar cv", exacta=True)],
                          [t("soporte aplicaciones"), t("soporte productivo"), t("mesa ayuda"), t("helpdesk")]])
    errores = validar_busquedas({"busquedas": [b]})
    assert any("7 operadores" in e for e in errores), errores


@pytest.mark.parametrize("consulta", [
    # 12/11, cuenta 2: con 6 o más OR LinkedIn devuelve «sin resultados», aun en consultas cortas
    "(funcional OR soporte OR helpdesk OR monitoreo OR incidentes OR analista OR ITIL OR ITSM)",
    '("cv a" OR "enviar cv") (funcional OR soporte OR helpdesk OR monitoreo OR incidentes OR analista)',
])
def test_seis_o_mas_or_se_rechaza(consulta):
    errores = validar_consulta(consulta)
    assert errores and any("operadores" in e and "5" in e for e in errores), errores


def test_los_or_entre_comillas_no_cuentan():
    assert validar_consulta('"a OR b OR c OR d OR e OR f OR g"') == []


def test_un_archivo_con_idioma_sigue_andando_y_el_idioma_se_ignora(data_dir):
    """Migración tolerante (12/11): el busquedas.json real puede traer todavía `idioma` (lo exigía el código
    anterior); se acepta, se ignora y no cambia nada. Los datos reales se tocan recién con el deploy."""
    escribir(data_dir, {"busquedas": [
        busqueda(nombre="A", idioma="es", consulta='"cv a"'),
        busqueda(nombre="B", idioma="en", consulta='"send your cv"'),
        busqueda(nombre="C", idioma="pt", consulta='"cv a" mesa'),
    ]})
    assert rc.validar_busquedas(json.loads((data_dir / "candidato" / "busquedas.json").read_text(encoding="utf-8"))) == []
    assert rc.cargar_busquedas() == [
        {"nombre": "A", "activa": True, "consulta": '"cv a"'},
        {"nombre": "B", "activa": True, "consulta": '"send your cv"'},
        {"nombre": "C", "activa": True, "consulta": '"cv a" mesa'},
    ]
    assert rc.consultas_activas() == ['"cv a"', '"send your cv"', '"cv a" mesa']


# ------------------------------------------------------------------ bordes de forma (S5: cobertura del código vivo)

def test_una_busqueda_o_un_termino_que_no_son_objetos_dan_su_error():
    errores = validar_busquedas({"busquedas": ["suelta", busqueda(bloques=[["texto suelto"]])]})
    assert "Búsqueda 1: tiene que ser un objeto con nombre, activa y bloques o consulta." in errores
    assert any("bloque 1, término 1: tiene que ser un objeto con «texto»" in e for e in errores), errores


def test_busquedas_de_portal_saltea_nombres_vacios_y_repetidos(data_dir):
    def con_portal(nombre, palabras):
        return {"nombre": nombre, "activa": True, "consulta": "\"cv a\"", "portales": ["indeed"], "palabras": palabras}
    escribir(data_dir, {"busquedas": [con_portal("Análisis", "analista"), con_portal("  ", "vacía"),
                                      con_portal("análisis", "repetida")]})
    assert rc.busquedas_de_portal("indeed") == [{"nombre": "Análisis", "palabras": "analista"}]
