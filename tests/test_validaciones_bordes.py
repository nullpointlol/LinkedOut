"""Bordes de validación de los archivos del perfil y utilidades chicas: cada error dice qué campo está mal."""
import json
import socket
from datetime import datetime, timezone

import pytest

from core import acceso_remoto, candidato_datos as cd, estructura_cv as ecv, portales_store


# ------------------------------------------------------------------ estructura_cv.json

@pytest.mark.parametrize("dato,error", [
    ([], "Tiene que ser un objeto"),
    ({"empresas": "Acme"}, "«empresas» tiene que ser una lista"),
    ({"empresas": [{"cargos": "todos"}]}, "Empresa 1: falta «empresa»"),
    ({"empresas": [], "cv_simple": []}, "«cv_simple» tiene que ser un objeto"),
    ({"empresas": [], "cv_simple": {"secciones_habilidades": []}}, "«secciones_habilidades» tiene que ser una lista"),
    ({"empresas": [], "cv_simple": {"titulo_habilidades": " "}}, "«titulo_habilidades» tiene que ser un texto"),
    ({"empresas": [], "cv_simple": {"activo": "sí"}}, "«activo» tiene que ser true o false"),
    ({"empresas": [], "cv_simple": {"titular": 3}}, "«titular» tiene que ser un texto"),
])
def test_estructura_cv_invalida(dato, error):
    assert any(error in e for e in ecv.validar(dato)), ecv.validar(dato)


def test_estructura_cv_sin_archivo_y_con_json_roto(tmp_path):
    assert ecv.leer(tmp_path) == ecv.Estructura()
    (tmp_path / ecv.ARCHIVO).write_text("{roto", encoding="utf-8")
    with pytest.raises(ecv.EstructuraInvalida, match="no es un JSON válido"):
        ecv.leer(tmp_path)


# ------------------------------------------------------------------ stack, roles y conocimientos ausentes

def test_stack_invalido_marca_cada_campo():
    assert cd.validar_stack([]) == ["El stack tiene que ser un objeto JSON."]
    errores = cd.validar_stack({"nota": 3, "coincidencias": ["no es objeto", {"nombre": "SQL", "palabras": [3],
                                                                              "justificacion": "x"}],
                                "transferibles": "no es lista", "habilidades_base": []})
    for esperado in ("«nota» tiene que ser texto", "coincidencias 1: tiene que ser un objeto",
                     "«transferibles» tiene que ser una lista", "«habilidades_base» tiene que ser un objeto",
                     "coincidencias 2: palabras"):
        assert any(esperado in e for e in errores), (esperado, errores)


def test_roles_invalidos_marca_cada_campo():
    assert cd.validar_roles("x") == ["Los roles objetivo tienen que ser un objeto JSON."]
    assert any("«nota» tiene que ser texto" in e for e in cd.validar_roles({"nota": [], "areas": []}))
    assert any("«areas» tiene que ser una lista" in e for e in cd.validar_roles({"areas": {}}))
    assert any("área 1: tiene que ser un objeto" in e for e in cd.validar_roles({"areas": ["x"]}))


def test_conocimientos_ausentes_invalidos():
    assert cd.validar_conocimientos_ausentes(3) == ["Los conocimientos ausentes tienen que ser un objeto JSON."]
    assert any("«nota» tiene que ser texto" in e for e in cd.validar_conocimientos_ausentes({"nota": 1}))
    errores = cd.validar_conocimientos_ausentes({"conocimientos": ["Kubernetes", "kubernetes"]})
    assert any("repetido" in e for e in errores)
    assert any("vacío" in e for e in cd.validar_conocimientos_ausentes({"conocimientos": ["="]}))
    assert any("texto no vacío" in e for e in cd.validar_conocimientos_ausentes({"conocimientos": [""]}))


# ------------------------------------------------------------------ acceso remoto

class _Socket:
    def __init__(self, ip=None, error=False):
        self.ip, self.error = ip, error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def connect(self, destino):
        if self.error:
            raise OSError("sin red")

    def getsockname(self):
        return (self.ip, 0)


@pytest.mark.parametrize("sock,esperado", [
    (_Socket("192.168.1.20"), "192.168.1.20"), (_Socket("127.0.0.1"), None), (_Socket("0.0.0.0"), None),
    (_Socket(error=True), None),
])
def test_ip_lan(monkeypatch, sock, esperado):
    monkeypatch.setattr(acceso_remoto.socket, "socket", lambda *a: sock)
    assert acceso_remoto.ip_lan() == esperado


# ------------------------------------------------------------------ portales_store

def test_ahora_iso_en_utc_aunque_venga_sin_zona():
    assert portales_store.ahora_iso(datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)) == "2026-11-17T03:00:00+00:00"
    assert portales_store.ahora_iso(datetime(2026, 11, 17, 3, 0)).endswith("+00:00")


def test_ya_vistos_sin_urns_no_abre_la_base(tmp_path):
    assert portales_store.ya_vistos(["", None], db_path=tmp_path / "no-existe.db") == set()


def test_estructura_cv_valida_desde_archivo_y_vinetas_invalidas(tmp_path):
    assert any("«vinetas_por_cargo» tiene que ser un entero" in e
               for e in ecv.validar({"empresas": [], "cv_simple": {"vinetas_por_cargo": 0}}))
    (tmp_path / ecv.ARCHIVO).write_text(json.dumps({"empresas": [{"empresa": "Acme"}]}), encoding="utf-8")
    assert isinstance(ecv.leer(tmp_path), ecv.Estructura)
