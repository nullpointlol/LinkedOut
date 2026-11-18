"""Estructura de la experiencia en el CV (creador, 15/11): cómo se muestran los cargos de cada empresa.

Vive en el perfil del candidato (`candidato/estructura_cv.json`, editable en Mi perfil); el código no trae reglas de
ninguna empresa. Formato:

    {"cargos_relevantes_entre": 10,
     "empresas": [
        {"empresa": "Acme", "cargos": "todos"},
        {"empresa": "Initech", "cargos": "segun_oferta", "titulo_unificado": "Analista de Operaciones",
         "titulo_unificado_en": "Operations Analyst", "vinetas_unificado": 2},
        {"empresa": "Globex", "cargos": "todos", "vinetas_max_por_cargo": 1, "largo_max_vineta": 110}]}

- `cargos`: «todos» (cada cargo con su título y sus fechas reales, nunca agrupados) o «segun_oferta» (los cargos
  separados si cada uno suma para la oferta; si no, un solo bloque con `titulo_unificado` y el período de la empresa).
- Un cargo «suma» si alguna de sus viñetas está entre las `cargos_relevantes_entre` de más puntaje del maestro.
- `vinetas_max_por_cargo`, `vinetas_unificado`, `largo_max_vineta`: topes (0 o ausente = sin tope).
- Una empresa que no figura muestra todos sus cargos: lo verdadero es el valor por defecto.
Siempre queda al menos 1 viñeta por cargo.

CV simple (creador, 15/11): para un puesto simple (lo decide la IA de la selección), el bloque `cv_simple` dice cómo
se arma; sin él, el CV va completo:

    "cv_simple": {"vinetas_por_cargo": 1, "secciones_habilidades": ["habilidades interpersonales"],
                  "lineas_habilidades": 3, "llenar_pagina": false,
                  "titulo_habilidades": "HABILIDADES", "titulo_habilidades_en": "SKILLS",
                  "titular": "", "titular_en": "", "activo": true, "un_cargo_por_empresa": true}

En pantalla se llama «CV general» (ofertas no técnicas o ajenas al perfil). Opcionales:
- `titular`/`titular_en`: la línea bajo el nombre, en vez del rol técnico; vacío, sin titular.
- `activo` (true si falta): false = la IA no elige el tipo y todos los CV van completos.
- `un_cargo_por_empresa`: un solo bloque por empresa, con el cargo que la IA nombra para la oferta a partir de los
  cargos reales de esa empresa, el período de la empresa y su mejor viñeta.
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ARCHIVO = "estructura_cv.json"
TODOS = "todos"
SEGUN_OFERTA = "segun_oferta"
CARGOS = (TODOS, SEGUN_OFERTA)
RELEVANTES_POR_DEFECTO = 10
_ENTEROS = ("vinetas_max_por_cargo", "vinetas_unificado", "largo_max_vineta")


class EstructuraInvalida(Exception):
    """El archivo de estructura del CV no es válido: no se genera hasta corregirlo."""


@dataclass(frozen=True)
class ReglaEmpresa:
    empresa: str
    cargos: str = TODOS
    titulo_unificado: str = ""
    titulo_unificado_en: str = ""
    vinetas_max_por_cargo: int = 0
    vinetas_unificado: int = 0
    largo_max_vineta: int = 0

    def titulo(self, ingles: bool) -> str:
        return (self.titulo_unificado_en if ingles else self.titulo_unificado).strip()


@dataclass(frozen=True)
class CVSimple:
    vinetas_por_cargo: int
    secciones_habilidades: tuple
    lineas_habilidades: int
    llenar_pagina: bool
    titulo_habilidades: str
    titulo_habilidades_en: str
    titular: str = ""
    titular_en: str = ""
    un_cargo_por_empresa: bool = False

    def titular_de(self, ingles: bool) -> str:
        return self.titular_en if ingles else self.titular

    def titulo(self, ingles: bool) -> str:
        return self.titulo_habilidades_en if ingles else self.titulo_habilidades


@dataclass(frozen=True)
class Estructura:
    empresas: Dict[str, ReglaEmpresa] = field(default_factory=dict)
    cargos_relevantes_entre: int = RELEVANTES_POR_DEFECTO
    cv_simple: Optional[CVSimple] = None

    def regla(self, empresa: str) -> ReglaEmpresa:
        return self.empresas.get(_clave(empresa)) or ReglaEmpresa(empresa)


def _clave(empresa: str) -> str:
    sin = unicodedata.normalize("NFKD", empresa or "").encode("ascii", "ignore").decode()
    return " ".join(sin.casefold().split())


def _entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0


def validar(dato: Any) -> List[str]:
    """Los errores del contenido (vacía = válido), en castellano y con el campo."""
    if not isinstance(dato, dict):
        return ["Tiene que ser un objeto con «empresas»."]
    errores: List[str] = []
    relevantes = dato.get("cargos_relevantes_entre", RELEVANTES_POR_DEFECTO)
    if not _entero(relevantes) or relevantes < 1:
        errores.append("«cargos_relevantes_entre» tiene que ser un entero mayor que 0.")
    empresas = dato.get("empresas", [])
    if not isinstance(empresas, list):
        return errores + ["«empresas» tiene que ser una lista."]
    vistas = set()
    for i, e in enumerate(empresas, 1):
        if not isinstance(e, dict) or not str(e.get("empresa") or "").strip():
            errores.append(f"Empresa {i}: falta «empresa».")
            continue
        nombre = str(e["empresa"]).strip()
        if _clave(nombre) in vistas:
            errores.append(f"«{nombre}» está repetida.")
        vistas.add(_clave(nombre))
        if e.get("cargos", TODOS) not in CARGOS:
            errores.append(f"«{nombre}»: «cargos» tiene que ser «todos» o «segun_oferta».")
        if e.get("cargos") == SEGUN_OFERTA and not (str(e.get("titulo_unificado") or "").strip()
                                                    and str(e.get("titulo_unificado_en") or "").strip()):
            errores.append(f"«{nombre}»: con «segun_oferta» hacen falta «titulo_unificado» y «titulo_unificado_en».")
        for campo in _ENTEROS:
            if campo in e and e[campo] not in (None, "") and not _entero(e[campo]):
                errores.append(f"«{nombre}»: «{campo}» tiene que ser un entero (0 = sin tope).")
    if "cv_simple" in dato:
        errores.extend(_errores_cv_simple(dato["cv_simple"]))
    return errores


_CAMPOS_SIMPLE = ("vinetas_por_cargo", "secciones_habilidades", "lineas_habilidades", "llenar_pagina",
                  "titulo_habilidades", "titulo_habilidades_en")
_OPCIONALES_SIMPLE = ("titular", "titular_en")
_BOOLEANOS_SIMPLE = ("activo", "un_cargo_por_empresa")


def _errores_cv_simple(c: Any) -> List[str]:
    if not isinstance(c, dict):
        return ["«cv_simple» tiene que ser un objeto."]
    errores = [f"cv_simple: campo desconocido «{k}»." for k in c
               if k not in _CAMPOS_SIMPLE + _OPCIONALES_SIMPLE + _BOOLEANOS_SIMPLE]
    errores += [f"cv_simple: falta «{k}»." for k in _CAMPOS_SIMPLE if k not in c]
    for k in ("vinetas_por_cargo", "lineas_habilidades"):
        if k in c and not (_entero(c[k]) and c[k] >= 1):
            errores.append(f"cv_simple: «{k}» tiene que ser un entero mayor que 0.")
    secciones = c.get("secciones_habilidades")
    if "secciones_habilidades" in c and not (isinstance(secciones, list) and secciones
                                             and all(isinstance(s, str) and s.strip() for s in secciones)):
        errores.append("cv_simple: «secciones_habilidades» tiene que ser una lista de secciones del maestro.")
    if "llenar_pagina" in c and not isinstance(c["llenar_pagina"], bool):
        errores.append("cv_simple: «llenar_pagina» tiene que ser true o false.")
    for k in ("titulo_habilidades", "titulo_habilidades_en"):
        if k in c and not (isinstance(c[k], str) and c[k].strip()):
            errores.append(f"cv_simple: «{k}» tiene que ser un texto.")
    for k in _OPCIONALES_SIMPLE:
        if k in c and not isinstance(c[k], str):
            errores.append(f"cv_simple: «{k}» tiene que ser un texto (vacío: sin titular).")
    for k in _BOOLEANOS_SIMPLE:
        if k in c and not isinstance(c[k], bool):
            errores.append(f"cv_simple: «{k}» tiene que ser true o false.")
    return errores


def desde_dato(dato: Any) -> Estructura:
    errores = validar(dato)
    if errores:
        raise EstructuraInvalida(f"{ARCHIVO}: " + " ".join(errores))
    empresas = {}
    for e in dato.get("empresas") or []:
        regla = ReglaEmpresa(
            empresa=str(e["empresa"]).strip(), cargos=e.get("cargos", TODOS),
            titulo_unificado=str(e.get("titulo_unificado") or ""), titulo_unificado_en=str(e.get("titulo_unificado_en") or ""),
            **{c: int(e.get(c) or 0) for c in _ENTEROS})
        empresas[_clave(regla.empresa)] = regla
    c = dato.get("cv_simple")
    simple = CVSimple(c["vinetas_por_cargo"], tuple(s.strip() for s in c["secciones_habilidades"]),
                      c["lineas_habilidades"], c["llenar_pagina"], c["titulo_habilidades"].strip(),
                      c["titulo_habilidades_en"].strip(), str(c.get("titular") or "").strip(),
                      str(c.get("titular_en") or "").strip(), c.get("un_cargo_por_empresa") is True
                      ) if isinstance(c, dict) and c.get("activo", True) is not False else None
    return Estructura(empresas, int(dato.get("cargos_relevantes_entre", RELEVANTES_POR_DEFECTO)), simple)


def leer(candidato: Path) -> Estructura:
    """La estructura del perfil; sin archivo, todas las empresas con todos sus cargos."""
    ruta = Path(candidato) / ARCHIVO
    if not ruta.exists():
        return Estructura()
    try:
        dato = json.loads(ruta.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise EstructuraInvalida(f"{ARCHIVO} no es un JSON válido: {exc}") from exc
    return desde_dato(dato)


def por_defecto(estructura: Optional[Estructura]) -> Estructura:
    return estructura if estructura is not None else Estructura()
