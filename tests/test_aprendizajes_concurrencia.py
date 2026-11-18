"""Carreras de aprendizajes con archivos reales aislados y esperas acotadas."""

import json
import os
import threading
from pathlib import Path

import pytest

from core import aprendizajes, paths, perfil_candidato


@pytest.fixture
def archivo(tmp_path, monkeypatch):
    candidato = tmp_path / "candidato"
    candidato.mkdir()
    destino = candidato / "aprendizajes_candidato.json"
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(aprendizajes, "APRENDIZAJES_FILE", destino)
    destino.write_text(json.dumps({
        "a": {"tag": "A", "estado": "TRANSFERIBLE", "eliminatorio": False},
        "b": {"tag": "B", "estado": "MATCH_DIRECTO"},
    }), encoding="utf-8")
    return destino


def _hilo(accion):
    resultado = {}

    def ejecutar():
        try:
            resultado["valor"] = accion()
        except BaseException as exc:
            resultado["error"] = exc

    hilo = threading.Thread(target=ejecutar, daemon=True)
    hilo.start()
    return hilo, resultado


def _terminar(tareas):
    for hilo, _ in tareas:
        hilo.join(timeout=10)
    assert all(not hilo.is_alive() for hilo, _ in tareas), "Operación colgada"
    for _, resultado in tareas:
        if "error" in resultado:
            raise resultado["error"]
    return [resultado["valor"] for _, resultado in tareas]


@pytest.mark.parametrize("repeticion", range(20))
@pytest.mark.parametrize("primero", ["borrar", "registrar"])
def test_borrar_y_registrar_concurrentes_no_pierden_datos(
        archivo, monkeypatch, primero, repeticion):
    # Sin el candado común: borrar primero pierde C; registrar primero revive A.
    original = json.loads(archivo.read_text(encoding="utf-8"))
    lectura_lista = threading.Barrier(2, timeout=5)
    segundo_en_curso = threading.Event()
    identidad = {}
    leer_real = aprendizajes.obtener_aprendizajes
    lock_real = getattr(aprendizajes, "LOCK", threading.RLock())

    class LockObservado:
        def __enter__(self):
            # Avisar antes de bloquear: el primer escritor puede continuar.
            if threading.get_ident() == identidad.get("segundo"):
                segundo_en_curso.set()
            return lock_real.__enter__()

        def __exit__(self, *args):
            return lock_real.__exit__(*args)

    def leer_demorado():
        datos = leer_real()
        if threading.get_ident() == identidad.get("primero"):
            lectura_lista.wait()
            assert segundo_en_curso.wait(timeout=5), "No arrancó el segundo escritor"
        return datos

    monkeypatch.setattr(aprendizajes, "LOCK", LockObservado(), raising=False)
    monkeypatch.setattr(aprendizajes, "obtener_aprendizajes", leer_demorado)
    acciones = {
        "borrar": lambda: perfil_candidato.borrar_aprendizaje("a"),
        "registrar": lambda: aprendizajes.registrar_aprendizaje("c", "MATCH_DIRECTO"),
    }
    segundo = "registrar" if primero == "borrar" else "borrar"

    def ejecutar_primero():
        identidad["primero"] = threading.get_ident()
        return acciones[primero]()

    def ejecutar_segundo():
        identidad["segundo"] = threading.get_ident()
        lectura_lista.wait()
        try:
            return acciones[segundo]()
        finally:
            # En el código sin candado, dejar terminar al segundo antes de
            # reanudar la escritura del snapshot viejo del primero.
            segundo_en_curso.set()

    resultados = _terminar([_hilo(ejecutar_primero), _hilo(ejecutar_segundo)])
    final = json.loads(archivo.read_text(encoding="utf-8"))
    assert set(final) == {"b", "c"}
    assert final["b"] == original["b"]
    assert final["c"]["tag"] == "c"
    assert final["c"]["estado"] == "MATCH_DIRECTO"
    borrado = resultados[0 if primero == "borrar" else 1]
    assert borrado["ok"] is True
    respaldo = paths.DATA_DIR / "backups" / borrado["backup"] / archivo.name
    anterior = json.loads(respaldo.read_text(encoding="utf-8"))
    assert anterior["a"] == original["a"]
    assert anterior["b"] == original["b"]
    if primero == "registrar":
        assert anterior["c"] == final["c"]


def test_guardar_usa_temporal_unico(archivo, monkeypatch):
    # Retener ambos temporales antes de replace detecta colisiones reales.
    reemplazar_real = os.replace
    escritos = threading.Barrier(2, timeout=5)
    reemplazo = threading.Lock()
    temporales = []
    datos = [{"uno": {"tag": "Ñ", "estado": "MATCH_DIRECTO"}},
             {"dos": {"tag": "Dos", "estado": "TRANSFERIBLE"}}]

    def reemplazar(origen, destino):
        temporal = Path(origen)
        assert Path(destino) == archivo
        assert temporal.parent == archivo.parent
        assert temporal.suffix == ".tmp"
        temporales.append(temporal)
        escritos.wait()
        # Windows puede rechazar dos reemplazos simultáneos del mismo destino.
        # Ambos temporales ya coexisten; solo serializar el replace real, como
        # hacen los escritores de producción bajo aprendizajes.LOCK.
        with reemplazo:
            return reemplazar_real(origen, destino)

    monkeypatch.setattr(os, "replace", reemplazar)
    resultados = _terminar([
        _hilo(lambda: aprendizajes._guardar_aprendizajes(datos[0])),
        _hilo(lambda: aprendizajes._guardar_aprendizajes(datos[1])),
    ])
    assert len(temporales) == len(set(temporales)) == 2
    assert resultados == [True, True]
    assert json.loads(archivo.read_text(encoding="utf-8")) in datos
    assert not list(archivo.parent.glob("*.tmp"))

    anterior = archivo.read_bytes()

    def fallar_replace(origen, destino):
        assert json.loads(Path(origen).read_text(encoding="utf-8")) == datos[0]
        raise OSError("Reemplazo fallido de prueba")

    monkeypatch.setattr(os, "replace", fallar_replace)
    assert aprendizajes._guardar_aprendizajes(datos[0]) is False
    assert archivo.read_bytes() == anterior
    assert not list(archivo.parent.glob("*.tmp"))
