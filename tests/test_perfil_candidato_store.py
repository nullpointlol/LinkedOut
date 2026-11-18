"""Contrato previo a implementar Mi perfil; exclusivamente datos sintéticos.

Plan de validación: medir motor/escritor actual; fijar lectura y validación;
observar backup antes de replace; inyectar fallos y competir con igual versión.
El kit ejecuta las bases con INVARIANT_CHECK antes de habilitar los defectos.
"""
import datetime as fechas
import hashlib
import importlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from core import aprendizajes, descartes, paths, perfil_areas, profile_parser


ARCHIVOS = {
    "experiencia": "experiencia.txt",
    "personales": "personales.txt",
    "restricciones": "restricciones.json",
    "anios_por_area_creador": "anios_por_area_creador.json",
    "voz_email": "voz_email.md",
}
EXPERIENCIA = "## Empresa ficticia\r\n### Soporte | 01/2018 - 01/2025\r\nEnseñé soporte.\r\n"
RESTRICCIONES = {
    "base": "Villa Prueba", "reubicacion": False,
    "edad": {"actual": 36, "al": "2026-11-09", "nota": "conservar"},
    "extra": {"preferencias": ["mañana"]},
}
AREAS = [{"area": "  Soporte  técnico ", "anios": " más  de  3 ",
          "reemplaza": ["incidentes"], "nota": "conservar"}]
CONFLICTO = "El archivo cambió desde que lo abriste. Recargá antes de guardar."
AVISO = "Los años por área se recalculan con IA en el próximo CV."


def _json(dato):
    return json.dumps(dato, ensure_ascii=False, indent=2) + "\r\n"


def _hash(contenido):
    return hashlib.sha256(contenido).hexdigest()


def _estado(carpeta):
    """Incluye directorios vacíos: un rechazo tampoco puede crear backups."""
    return {str(p.relative_to(carpeta)): p.read_bytes() if p.is_file() else None
            for p in carpeta.rglob("*")}


def _primer_nivel(carpeta):
    return {p.name: p.read_bytes() for p in carpeta.iterdir() if p.is_file()}


def _sin_ia(*args, **kwargs):
    raise AssertionError("Leer o guardar el perfil no puede invocar IA")


@pytest.fixture
def candidato(tmp_path, monkeypatch):
    raiz = tmp_path / "datos"
    carpeta = raiz / "candidato"
    carpeta.mkdir(parents=True)
    contenidos = {
        "experiencia.txt": EXPERIENCIA,
        "personales.txt": "Nombre: Ana Prueba\r\nPuesto: Técnica\r\n",
        "restricciones.json": _json(RESTRICCIONES),
        "anios_por_area_creador.json": _json({"anios_por_area": AREAS, "nota": "prueba"}),
        "voz_email.md": "Hola, acompañé proyectos.\r\nSaludos.\r\n",
        "aprendizajes_candidato.json": _json({"ci/cd": {"tag": "CI/CD", "estado": "TRANSFERIBLE"}}),
    }
    for nombre, texto in contenidos.items():
        (carpeta / nombre).write_bytes(texto.encode("utf-8"))
    (carpeta / "extra.bin").write_bytes(b"\x00\xff\x01")
    (carpeta / ".oculto").write_bytes(b"tambien se respalda")
    (carpeta / "subcarpeta").mkdir()
    (carpeta / "subcarpeta" / "interno.txt").write_bytes(b"no es primer nivel")
    monkeypatch.setattr(paths, "DATA_DIR", raiz)
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", carpeta / "aprendizajes_candidato.json")
    monkeypatch.setattr(descartes, "RESTRICCIONES_FILE", carpeta / "restricciones.json")

    class Fecha(fechas.date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 10)

    monkeypatch.setattr(descartes, "date", Fecha)
    monkeypatch.setattr(perfil_areas, "AntigravityBridge", _sin_ia)
    monkeypatch.setattr(perfil_areas, "asegurar_anios_por_area", _sin_ia)
    # invariante: el motor vigente acepta este objeto sin alterar claves desconocidas.
    assert descartes.cargar_restricciones() == RESTRICCIONES
    assert profile_parser.calcular_anios_experiencia(EXPERIENCIA) == 7
    assert perfil_areas._validar_areas(AREAS, 7) == [
        {"area": "Soporte técnico", "anios": "más de 3"}]
    assert perfil_areas.anios_por_area_en_cache(carpeta) == []
    return carpeta


def test_invariantes_motor_y_escritura_actual(candidato, monkeypatch):
    # invariante: el validador normaliza, elimina repetidos y limita a ocho, total 7.
    filas = [{"area": f"área {i}", "anios": "1"} for i in range(9)]
    assert perfil_areas._validar_areas(filas, 7) == filas[:8]
    assert perfil_areas._validar_areas([
        {"area": " Soporte ", "anios": " más  de  7 "},
        {"area": "SOPORTE", "anios": "2"},
        {"area": "otra", "anios": "8"},
        {"area": "otra", "anios": "siete"},
        {"area": 3, "anios": "1"},
        {"area": "otra", "anios": 1}, None,
    ], 7) == [{"area": "Soporte", "anios": "más de 7"}]
    assert descartes._validas({}) is True
    assert descartes._validas(RESTRICCIONES) is True
    for dato in ({"reubicacion": "no"}, {"reubicacion": 0},
                 {"edad": {"actual": True, "al": "2026-11-09"}},
                 {"edad": {"actual": 36, "al": "2026-11-11"}}):
        assert descartes._validas(dato) is False
    antes = _estado(candidato)
    assert perfil_areas.anios_por_area_en_cache(candidato) == []
    assert _estado(candidato) == antes

    # invariante medido: aprendizajes usa temporal + replace, devuelve bool y conserva
    # el destino ante fallo; este escenario no importa el módulo nuevo.
    archivo = aprendizajes.APRENDIZAJES_FILE
    datos = {"ñ": {"tag": "Prueba", "estado": "MATCH_DIRECTO"}}
    assert aprendizajes._guardar_aprendizajes(datos) is True
    assert aprendizajes.obtener_aprendizajes() == datos
    anterior = archivo.read_bytes()
    reemplazos = []
    replace_real = Path.replace

    def fallar_replace(origen, destino):
        reemplazos.append((origen, destino))
        assert origen != archivo
        assert origen.parent == archivo.parent
        assert destino == archivo
        assert archivo.read_bytes() == anterior
        assert json.loads(origen.read_bytes()) == {"nuevo": {}}
        raise OSError("replace de prueba")

    monkeypatch.setattr(Path, "replace", fallar_replace)
    assert aprendizajes._guardar_aprendizajes({"nuevo": {}}) is False
    assert len(reemplazos) == 1
    assert archivo.read_bytes() == anterior
    assert aprendizajes.obtener_aprendizajes() == datos
    assert not archivo.with_suffix(".tmp").exists()
    monkeypatch.setattr(Path, "replace", replace_real)


@pytest.mark.parametrize("clave,archivo", list(ARCHIVOS.items()))
def test_leer_bytes_y_ruta_dinamica(candidato, monkeypatch, clave, archivo):
    # invariante: lectura de restricciones y preview ya verificadas en candidato.
    original = (candidato / archivo).read_bytes()
    antes = _estado(candidato.parent)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = importlib.import_module("core.perfil_candidato")
    assert perfil.leer_perfil(clave) == {
        "clave": clave, "archivo": archivo, "contenido": original.decode("utf-8"),
        "version": _hash(original), "existe": True,
    }
    assert perfil.leer_anios_por_area() == {"areas": [], "vigente": False}
    assert _estado(candidato.parent) == antes

    # Redirigir DESPUÉS del import y de la primera lectura detecta rutas congeladas.
    nueva_raiz = candidato.parent / "otra_raiz"
    otra = nueva_raiz / "candidato"
    otra.mkdir(parents=True)
    distinto = "  otro contenido con ñ\r\n\r\n".encode("utf-8")
    (otra / archivo).write_bytes(distinto)
    monkeypatch.setattr(paths, "DATA_DIR", nueva_raiz)
    assert perfil.leer_perfil(clave) == {
        "clave": clave, "archivo": archivo, "contenido": distinto.decode("utf-8"),
        "version": _hash(distinto), "existe": True,
    }
    (otra / archivo).unlink()
    assert perfil.leer_perfil(clave) == {
        "clave": clave, "archivo": archivo, "contenido": "",
        "version": _hash(b""), "existe": False,
    }
    antes = _estado(nueva_raiz)
    for desconocida in ("desconocida", "../experiencia", "experiencia.txt", "", "EXPERIENCIA"):
        with pytest.raises(perfil.PerfilError) as error:
            perfil.leer_perfil(desconocida)
        assert error.value.status_code == 404
        assert isinstance(error.value.detail, str) and error.value.detail
    assert _estado(nueva_raiz) == antes
    assert (candidato / archivo).read_bytes() == original


GUARDADOS = [
    ("experiencia", "  Experiencia nueva, enseñé.\r\n"),
    ("personales", "  Nombre: otra persona\r\n"),
    ("voz_email", "  Hola, mañana.\r\n\r\n"),
    ("restricciones", ' { "extra": {"ñ": 1}, "reubicacion": false }\r\n'),
    ("restricciones", "{}"),
    ("anios_por_area_creador", _json({"anios_por_area": AREAS, "nota": "ñ", "extra": [1]})),
    ("anios_por_area_creador", _json([{"area": f"área {i}", "anios": "1"} for i in range(8)])),
    ("anios_por_area_creador", _json([{"area": "soporte", "anios": "más de 7"}])),
    ("anios_por_area_creador", "[]"),
    *[(clave, None) for clave in ARCHIVOS],
    *[(clave, "ñ" * 200000) for clave in ("experiencia", "personales", "voz_email")],
]


@pytest.mark.parametrize("clave,contenido", GUARDADOS,
                         ids=[f"{clave}-{i}" for i, (clave, _) in enumerate(GUARDADOS)])
def test_guardar_respalda_todo_antes_de_replace(candidato, monkeypatch, clave, contenido):
    # invariante: se preserva todo primer nivel, incluso binarios y archivos ocultos.
    anteriores = _primer_nivel(candidato)
    arbol_anterior = _estado(candidato)
    archivo = candidato / ARCHIVOS[clave]
    viejo = archivo.read_bytes()
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = importlib.import_module("core.perfil_candidato")
    contenido = viejo.decode("utf-8") if contenido is None else contenido
    nuevo = contenido.encode("utf-8")
    reemplazos = []
    replace_real = os.replace

    def observar_replace(origen, destino, *args, **kwargs):
        if Path(destino) == archivo:
            copias = list((candidato.parent / "backups").iterdir())
            assert len(copias) == 1
            assert _estado(copias[0]) == anteriores
            assert archivo.read_bytes() == viejo
            assert Path(origen) != archivo
            assert Path(origen).parent == candidato
            assert Path(origen).read_bytes() == nuevo
            reemplazos.append(Path(origen))
        return replace_real(origen, destino, *args, **kwargs)

    monkeypatch.setattr(os, "replace", observar_replace)
    resultado = perfil.guardar_perfil(clave, contenido, _hash(viejo))
    assert len(reemplazos) == 1
    assert resultado == {
        "ok": True, "version": _hash(nuevo), "backup": resultado["backup"],
        "aviso": AVISO if clave in ("experiencia", "personales") and nuevo != viejo else None,
    }
    assert Path(resultado["backup"]).name == resultado["backup"]
    assert resultado["backup"].startswith("candidato-")
    backup = candidato.parent / "backups" / resultado["backup"]
    assert _estado(backup) == anteriores
    arbol_anterior[archivo.name] = nuevo
    assert _estado(candidato) == arbol_anterior
    assert perfil.leer_perfil(clave)["version"] == _hash(nuevo)
    if clave == "restricciones":
        assert descartes.cargar_restricciones() == json.loads(contenido)
    if clave == "anios_por_area_creador":
        dato = json.loads(contenido)
        lista = dato["anios_por_area"] if isinstance(dato, dict) else dato
        assert len(perfil_areas._validar_areas(lista, 7)) == len(lista)


RECHAZOS = [
    ("desconocida", "nuevo", 404, None),
    ("../experiencia", "nuevo", 404, None),
    *[(clave, texto, 422, None) for clave in ("experiencia", "personales", "voz_email")
      for texto in ("", " \t\r\n", "ñ" * 200001)],
    *[(clave, "{roto", 422, "No es un JSON válido: ")
      for clave in ("restricciones", "anios_por_area_creador")],
    *[("restricciones", _json(dato), 422, "Restricciones inválidas") for dato in (
        [], None, {"base": " "}, {"reubicacion": "no"}, {"reubicacion": 0},
        {"viajes_frecuentes_o_interior": 1}, {"rubro_o_funcion_exigida_sin_experiencia": "sí"},
        {"edad": {"actual": 36, "al": "2026-11-11"}},
        {"edad": {"actual": True, "al": "2026-11-09"}},
        {"edad": {"actual": 0, "al": "2026-11-09"}},
        {"edad": {"actual": 130, "al": "2026-11-09"}},
        {"edad": {"actual": "36", "al": "2026-11-09"}},
        {"edad": {"actual": 36, "al": "fecha rota"}},
    )],
    *[("anios_por_area_creador", _json(dato), 422, None) for dato in (
        {}, {"anios_por_area": {}}, None, [None],
        [{"area": 1, "anios": "1"}], [{"area": "soporte", "anios": 1}],
        [{"area": "soporte", "anios": "1", "reemplaza": "incidentes"}],
        [{"area": "soporte", "anios": "1", "reemplaza": [1]}],
    )],
]
for _anios in ("siete", "8", "0", "1.5"):
    RECHAZOS.append(("anios_por_area_creador", _json([
        {"area": "válida", "anios": "1"}, {"area": "soporte", "anios": _anios}]), 422,
        f'Área inválida: “soporte” ({_anios}). Usá “N” o “más de N”, sin superar el total de 7 años.'))
RECHAZOS.extend([
    ("anios_por_area_creador", _json([
        {"area": "soporte", "anios": "1"}, {"area": "SOPORTE", "anios": "2"}]), 422,
     'Área inválida: “SOPORTE” (2). Usá “N” o “más de N”, sin superar el total de 7 años.'),
    ("anios_por_area_creador", _json([
        {"area": f"área {i}", "anios": "1"} for i in range(9)]), 422,
     'Área inválida: “área 8” (1). Usá “N” o “más de N”, sin superar el total de 7 años.'),
    *[(clave, "versión obsoleta", 409, CONFLICTO) for clave in ARCHIVOS],
])


@pytest.mark.parametrize("clave,contenido,status,detalle", RECHAZOS,
                         ids=[f"{clave}-{status}-{i}" for i, (clave, _, status, _) in enumerate(RECHAZOS)])
def test_conflicto_y_validacion_no_escriben(candidato, monkeypatch, clave, contenido, status, detalle):
    # invariante: motor vigente usa total 7 y datos válidos, antes del módulo nuevo.
    antes = _estado(candidato.parent)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = importlib.import_module("core.perfil_candidato")
    version = _hash((candidato / ARCHIVOS[clave]).read_bytes()) if clave in ARCHIVOS else _hash(b"")
    if status == 409:
        # Cambio externo posterior a la lectura, con contenido actual todavía válido.
        (candidato / ARCHIVOS[clave]).write_bytes(b"cambio externo\r\n")
        antes = _estado(candidato.parent)
    monkeypatch.setattr(perfil, "crear_backup", _sin_escritura)
    with pytest.raises(perfil.PerfilError) as error:
        perfil.guardar_perfil(clave, contenido, version)
    assert error.value.status_code == status
    assert isinstance(error.value.detail, str) and error.value.detail
    if detalle is not None:
        if detalle in ("No es un JSON válido: ", "Restricciones inválidas"):
            assert error.value.detail.startswith(detalle)
        else:
            assert error.value.detail == detalle
    assert _estado(candidato.parent) == antes


def _sin_escritura(*args, **kwargs):
    raise AssertionError("Una operación rechazada no puede comenzar el backup")


def test_colisiones_backup(candidato, monkeypatch):
    # invariante: el backup debe incluir los ocho archivos de primer nivel del fixture.
    anteriores = _primer_nivel(candidato)
    assert len(anteriores) == 8
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = importlib.import_module("core.perfil_candidato")
    datetime_real = fechas.datetime

    class RelojFijo(datetime_real):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 10, 2, 40, 0, tzinfo=tz)

    # Admite tanto `import datetime` como `from datetime import datetime`.
    for nombre, valor in list(vars(perfil).items()):
        if valor is datetime_real:
            monkeypatch.setattr(perfil, nombre, RelojFijo)
    monkeypatch.setattr(fechas, "datetime", RelojFijo)
    nombres = [perfil.crear_backup(candidato) for _ in range(3)]
    assert nombres == ["candidato-20261110-024000", "candidato-20261110-024000-2",
                       "candidato-20261110-024000-3"]
    for nombre in nombres:
        assert _estado(candidato.parent / "backups" / nombre) == anteriores
    # Una cuarta operación no modifica ninguno de los respaldos ya creados.
    viejo = (candidato / "personales.txt").read_bytes()
    resultado = perfil.guardar_perfil("personales", "Nombre: nuevo", _hash(viejo))
    assert resultado["backup"] == "candidato-20261110-024000-4"
    for nombre in [*nombres, resultado["backup"]]:
        assert _estado(candidato.parent / "backups" / nombre) == anteriores


@pytest.mark.parametrize("fallo", ["copia", "replace"])
@pytest.mark.parametrize("existe", [True, False])
def test_fallos_copia_y_replace(candidato, monkeypatch, fallo, existe):
    archivo = candidato / "voz_email.md"
    if not existe:
        archivo.unlink()
    # invariante: lectura sin caché sigue vacía aun con un archivo opcional ausente.
    assert perfil_areas.anios_por_area_en_cache(candidato) == []
    anteriores = _estado(candidato)
    archivos_anteriores = _primer_nivel(candidato)
    version = _hash(archivo.read_bytes() if existe else b"")
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = importlib.import_module("core.perfil_candidato")
    copyfile_real, replace_real = shutil.copyfile, os.replace
    copias, reemplazos = [], []

    def copiar(origen, destino, *args, **kwargs):
        if candidato.parent / "backups" in Path(destino).parents:
            copias.append(Path(destino))
            if fallo == "copia" and len(copias) == 2:
                raise OSError("copia de prueba")
        return copyfile_real(origen, destino, *args, **kwargs)

    def reemplazar(origen, destino, *args, **kwargs):
        if Path(destino) == archivo:
            reemplazos.append(Path(origen))
            assert _estado(Path(copias[0]).parent) == archivos_anteriores
            assert Path(origen).read_bytes() == "nuevo con ñ\r\n".encode("utf-8")
            raise OSError("replace de prueba")
        return replace_real(origen, destino, *args, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", copiar)
    monkeypatch.setattr(os, "replace", reemplazar)
    with pytest.raises(OSError) as error:
        perfil.guardar_perfil("voz_email", "nuevo con ñ\r\n", version)
    assert str(error.value) == f"{fallo} de prueba"
    assert len(copias) == (2 if fallo == "copia" else len(_primer_nivel(candidato)))
    assert len(reemplazos) == (0 if fallo == "copia" else 1)
    assert _estado(candidato) == anteriores  # sin destino truncado ni temporal abandonado


def test_dos_put_misma_version(candidato):
    # invariante: el contenido anterior existe y la restricción sigue siendo legible.
    anteriores = _primer_nivel(candidato)
    assert descartes.cargar_restricciones() == RESTRICCIONES
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = importlib.import_module("core.perfil_candidato")
    version = _hash(anteriores["personales.txt"])
    inicio = Barrier(2)

    def guardar(texto):
        inicio.wait(timeout=10)
        try:
            return texto, perfil.guardar_perfil("personales", texto, version)
        except perfil.PerfilError as error:
            return texto, error

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(guardar, "Nombre: primera, ñ\r\n")
        b = executor.submit(guardar, "Nombre: segunda, á\r\n")
        resultados = [a.result(timeout=10), b.result(timeout=10)]
    exitos = [(texto, r) for texto, r in resultados if isinstance(r, dict)]
    errores = [r for _, r in resultados if isinstance(r, perfil.PerfilError)]
    assert len(exitos) == 1
    assert len(errores) == 1
    assert errores[0].status_code == 409
    assert errores[0].detail == CONFLICTO
    ganador, respuesta = exitos[0]
    assert respuesta == {"ok": True, "version": _hash(ganador.encode("utf-8")),
                         "backup": respuesta["backup"], "aviso": AVISO}
    backups = list((candidato.parent / "backups").iterdir())
    assert [p.name for p in backups] == [respuesta["backup"]]
    assert _estado(backups[0]) == anteriores
    esperados = dict(anteriores, **{"personales.txt": ganador.encode("utf-8")})
    assert _primer_nivel(candidato) == esperados
    assert {p.name for p in candidato.iterdir()} == {*anteriores, "subcarpeta"}
    assert (candidato / "subcarpeta" / "interno.txt").read_bytes() == b"no es primer nivel"
