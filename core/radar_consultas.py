"""Búsquedas del Radar: 100 % definidas por el usuario (11/11).

El código no trae ninguna palabra de búsqueda propia. Las búsquedas salen de `<DATA_DIR>/candidato/busquedas.json`
(no versionado; ejemplo ficticio en `config/ejemplo/candidato/busquedas.json`). Sin ese archivo no hay búsquedas
y el Radar lee solo el feed.

Formato:
    {"busquedas": [
      {"nombre": "Pedido de CV", "activa": true, "consulta": "\\"cv a\\""},
      {"nombre": "Análisis", "activa": true, "bloques": [
        [{"texto": "cv a", "exacta": true}, {"texto": "enviar cv", "exacta": true}],
        [{"texto": "funcional"}, {"texto": "business analyst", "exacta": true}],
        [{"texto": "senior", "excluir": true}]
      ]}
    ]}

Cada búsqueda lleva `nombre` (único), `activa` (true/false) y, o bien `bloques`, o bien
una `consulta` libre que va tal cual. Dentro de un bloque los términos van con OR; entre bloques, AND (un espacio).
Por término: `texto`, `exacta` (entre comillas) y `excluir` (NOT). El bloque de arriba se arma así:
    ("cv a" OR "enviar cv") (funcional OR "business analyst") NOT senior

Funciones públicas (las usa también Mi perfil):
  * `armar_consulta(busqueda) -> str`: la consulta exacta que ejecuta el Radar.
  * `validar_busquedas(datos) -> list[str]`: errores en castellano; vacía si el archivo se puede guardar.
  * `validar_consulta(consulta) -> list[str]`: las reglas de sintaxis de LinkedIn sobre una consulta armada.
Las reglas (comillas rectas, paréntesis balanceados, AND/OR/NOT en mayúsculas, hasta 5 operadores, sin `*` ni corchetes,
largo máximo)
salen de las pruebas de `docs/linkedin-sintaxis.md`.

Mi perfil (rediseño E6): la lista entera se edita en Qué busco › Búsquedas del Radar (PUT /api/candidato/busquedas,
core.perfil_candidato.guardar_busquedas), con esta misma validación; static/perfil.js repite `armar_consulta` para
mostrar la consulta mientras se edita (tests/test_perfil_busquedas_editor.py controla que coincidan).

Portales (14/11): cada búsqueda puede llevar además `portales` (lista: "jooble", "indeed") y `palabras` (palabras
simples, sin operadores ni comillas: los portales no tienen sintaxis booleana). Con algún portal marcado, `palabras` es
obligatorio. Se editan desde Mi perfil. Un error en estos campos no frena la búsqueda de LinkedIn, solo la del portal.
  * `busquedas_de_portal(portal) -> list[dict]`: [{nombre, palabras}] de las búsquedas activas con ese portal.
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional

import core.paths

logger = logging.getLogger(__name__)

ARCHIVO = "busquedas.json"
OPERADORES = ("AND", "OR", "NOT")
# Largo máximo de una consulta armada (caracteres). Se ajusta con las pruebas de docs/linkedin-sintaxis.md
LARGO_MAXIMO = 300
# Máximo de operadores escritos (AND, OR y NOT) por consulta: con 6 o más, LinkedIn devuelve «sin resultados»
# aun en consultas cortas (12/11, cuenta 2: 8 palabras con OR en 89 caracteres, cero; 6 frases con OR, 21 posts;
# 4 OR + 2 AND, cero; 3 OR + 1 AND, 21). El AND implícito (un espacio) no cuenta. docs/linkedin-sintaxis.md
MAX_OPERADORES = 5
# Entre bloques: AND implícito (un espacio), como en las búsquedas que hoy traen resultados
SEPARADOR_BLOQUES = " "

_CLAVES_VIEJAS = ("variantes_envio", "ubicacion", "grupos_rol", "empresas", "roles_en_empresas", "consultas_ingles")
_COMILLAS_TIPOGRAFICAS = "“”„‟«»"
_PROHIBIDOS = "*[]{}"
_PROHIBIDOS_EN_TERMINO = '"()' + _PROHIBIDOS + _COMILLAS_TIPOGRAFICAS


# ------------------------------------------------------------------ armado

def _limpio(texto: Any) -> str:
    return " ".join(str(texto).split())


def _termino(termino: Dict[str, Any], en_or: bool) -> str:
    texto = _limpio(termino.get("texto", ""))
    if termino.get("exacta"):
        return f'"{texto}"'
    palabras = texto.split()
    if len(palabras) > 1 and (en_or or termino.get("excluir")):
        return "(" + " AND ".join(palabras) + ")"
    return texto


def armar_consulta(busqueda: Dict[str, Any]) -> str:
    """La consulta exacta que va a ejecutar el Radar para `busqueda` (ya validada o no)."""
    if "consulta" in busqueda and not busqueda.get("bloques"):
        return _limpio(busqueda.get("consulta") or "")
    partes: List[str] = []
    for bloque in busqueda.get("bloques") or []:
        incluidos = [x for x in bloque if isinstance(x, dict) and not x.get("excluir")]
        excluidos = [x for x in bloque if isinstance(x, dict) and x.get("excluir")]
        if len(incluidos) == 1:
            partes.append(_termino(incluidos[0], en_or=False))
        elif incluidos:
            partes.append("(" + " OR ".join(_termino(x, en_or=True) for x in incluidos) + ")")
        partes.extend("NOT " + _termino(x, en_or=False) for x in excluidos)
    return SEPARADOR_BLOQUES.join(p for p in partes if p)


# ------------------------------------------------------------------ validación

def _fichas(consulta: str) -> List[str]:
    """Separa la consulta en frases entre comillas, paréntesis y palabras (fuera de comillas)."""
    return re.findall(r'"[^"]*"|\(|\)|[^\s()"]+', consulta)


def validar_consulta(consulta: str) -> List[str]:
    """Errores de sintaxis de una consulta armada, según lo que acepta la búsqueda de LinkedIn."""
    texto = (consulta or "").strip()
    if not texto:
        return ["La consulta está vacía."]
    errores: List[str] = []
    if any(c in texto for c in _COMILLAS_TIPOGRAFICAS):
        errores.append("Usá comillas rectas (\") en vez de “ ”, „ o « ».")
    if texto.count('"') % 2:
        errores.append("Las comillas no cierran: cada frase exacta va entre dos comillas rectas.")
    if "*" in texto:
        errores.append("LinkedIn no acepta el comodín *.")
    if any(c in texto for c in "[]{}"):
        errores.append("LinkedIn no acepta corchetes ni llaves.")
    if len(texto) > LARGO_MAXIMO:
        errores.append(f"La consulta tiene {len(texto)} caracteres; el máximo es {LARGO_MAXIMO}.")
    if texto.count('"') % 2:
        return errores  # sin comillas parejas no se puede leer el resto

    fichas = _fichas(texto)
    nivel = 0
    for i, ficha in enumerate(fichas):
        if ficha == "(":
            nivel += 1
            if i + 1 < len(fichas) and fichas[i + 1] == ")":
                errores.append("Hay paréntesis vacíos: «()».")
        elif ficha == ")":
            nivel -= 1
            if nivel < 0:
                errores.append("Los paréntesis no están balanceados: sobra un «)».")
                nivel = 0
    if nivel > 0:
        errores.append("Los paréntesis no están balanceados: falta cerrar un «(».")

    minusculas = sorted({f for f in fichas if f.upper() in OPERADORES and f not in OPERADORES})
    if minusculas:
        errores.append("Los operadores AND, OR y NOT van en mayúsculas (encontré: "
                       + ", ".join(f"«{f}»" for f in minusculas) + ").")

    operadores = sum(1 for f in fichas if f in OPERADORES)
    if operadores > MAX_OPERADORES:
        errores.append(f"La consulta tiene {operadores} operadores (AND, OR o NOT); LinkedIn acepta hasta "
                       f"{MAX_OPERADORES} (con más no trae resultados). Repartí los términos en dos búsquedas o "
                       "marcá como frase exacta los términos de varias palabras.")

    def es_op(f: str) -> bool:
        return f in OPERADORES

    sueltos = False
    for i, ficha in enumerate(fichas):
        if not es_op(ficha):
            continue
        antes = fichas[i - 1] if i > 0 else None
        despues = fichas[i + 1] if i + 1 < len(fichas) else None
        # «a AND NOT b» vale: un AND/OR puede ir seguido de NOT
        falta_despues = (despues is None or despues == ")"
                         or (es_op(despues) and not (despues == "NOT" and ficha != "NOT")))
        falta_antes = ficha != "NOT" and (antes is None or antes == "(" or es_op(antes))
        if falta_despues or falta_antes:
            sueltos = True
    if sueltos:
        errores.append("Hay un operador (AND, OR o NOT) sin un término a cada lado.")
    return errores


def _es_bool(valor: Any) -> bool:
    return isinstance(valor, bool)


def _errores_de_busqueda(b: Any, indice: int) -> List[str]:
    """Errores de una búsqueda sola (sin mirar nombres repetidos)."""
    if not isinstance(b, dict):
        return [f"Búsqueda {indice}: tiene que ser un objeto con nombre, activa y bloques o consulta."]
    nombre = b.get("nombre")
    nombre_ok = isinstance(nombre, str) and nombre.strip()
    prefijo = f"«{nombre.strip()}»" if nombre_ok else f"Búsqueda {indice}"
    errores: List[str] = []
    if not nombre_ok:
        errores.append("falta el nombre.")
    if not _es_bool(b.get("activa")):
        errores.append("«activa» tiene que ser true o false.")

    tiene_bloques, tiene_consulta = "bloques" in b, "consulta" in b
    if tiene_bloques and tiene_consulta:
        errores.append("lleva bloques o consulta libre, solo una de las dos.")
    elif tiene_consulta:
        if not isinstance(b["consulta"], str):
            errores.append("la consulta libre tiene que ser un texto.")
    elif not tiene_bloques or not isinstance(b["bloques"], list) or not b["bloques"]:
        errores.append("faltan los bloques de términos (o una consulta libre).")
    else:
        incluidos = 0
        for nb, bloque in enumerate(b["bloques"], start=1):
            if not isinstance(bloque, list) or not bloque:
                errores.append(f"el bloque {nb} está vacío.")
                continue
            for nt, termino in enumerate(bloque, start=1):
                donde = f"bloque {nb}, término {nt}"
                if not isinstance(termino, dict):
                    errores.append(f"{donde}: tiene que ser un objeto con «texto».")
                    continue
                texto = termino.get("texto")
                limpio = _limpio(texto) if isinstance(texto, str) else ""
                if not limpio:
                    errores.append(f"{donde}: está vacío.")
                elif any(c in limpio for c in _PROHIBIDOS_EN_TERMINO):
                    errores.append(f"{donde}: no puede llevar comillas, paréntesis, * ni corchetes "
                                   "(para frase exacta usá la casilla).")
                elif limpio.upper() in OPERADORES:
                    errores.append(f"{donde}: «{limpio}» es un operador, no un término.")
                for clave in ("exacta", "excluir"):
                    if clave in termino and not _es_bool(termino[clave]):
                        errores.append(f"{donde}: «{clave}» tiene que ser true o false.")
                if not termino.get("excluir"):
                    incluidos += 1
        if incluidos == 0 and not errores:
            errores.append("todos los términos son para excluir: falta al menos uno a buscar.")

    if not errores:
        errores.extend(e[0].lower() + e[1:] for e in validar_consulta(armar_consulta(b)))
    return [f"{prefijo}: {e}" for e in errores]


# Los portales del perfil (core/reglas_portales.py), una sola lista
from core.reglas_portales import PORTALES  # noqa: E402
# Palabras simples para los portales: sin operadores, comillas, paréntesis ni comodines
_PROHIBIDOS_EN_PALABRAS = '"()' + _PROHIBIDOS + _COMILLAS_TIPOGRAFICAS
LARGO_MAXIMO_PALABRAS = 100


def errores_de_portales(b: Any, indice: int) -> List[str]:
    """Errores de `portales` y `palabras` de una búsqueda (vacía: bien o no los usa)."""
    if not isinstance(b, dict):
        return []
    nombre = b.get("nombre")
    prefijo = f"«{nombre.strip()}»" if isinstance(nombre, str) and nombre.strip() else f"Búsqueda {indice}"
    errores: List[str] = []
    portales = b.get("portales", [])
    if not isinstance(portales, list) or any(p not in PORTALES for p in portales):
        errores.append(f"«portales» tiene que ser una lista con {' o '.join(PORTALES)}.")
        portales = []
    elif len(set(portales)) != len(portales):
        errores.append("un portal está repetido.")
    palabras = b.get("palabras", "")
    if not isinstance(palabras, str):
        errores.append("«palabras» tiene que ser un texto.")
    else:
        limpio = _limpio(palabras)
        if any(c in limpio for c in _PROHIBIDOS_EN_PALABRAS):
            errores.append("las palabras para portales van sin comillas, paréntesis, * ni corchetes.")
        elif any(f in OPERADORES for f in limpio.split()):
            errores.append("las palabras para portales van sin AND, OR ni NOT.")
        elif len(limpio) > LARGO_MAXIMO_PALABRAS:
            errores.append(f"las palabras para portales tienen más de {LARGO_MAXIMO_PALABRAS} caracteres.")
        elif portales and not limpio:
            errores.append("faltan las palabras para los portales marcados.")
    return [f"{prefijo}: {e}" for e in errores]


def validar_busquedas(datos: Any) -> List[str]:
    """Errores del contenido de `busquedas.json` (en castellano). Lista vacía: se puede guardar."""
    if isinstance(datos, dict) and "busquedas" not in datos and any(k in datos for k in _CLAVES_VIEJAS):
        return ["El archivo tiene el formato viejo (variantes_envio, grupos_rol, empresas…): pasalo al formato "
                "nuevo, con la lista «busquedas»."]
    if not isinstance(datos, dict) or not isinstance(datos.get("busquedas"), list):
        return ["Falta la lista «busquedas» (puede estar vacía)."]
    errores: List[str] = []
    vistos: Dict[str, int] = {}
    for indice, b in enumerate(datos["busquedas"], start=1):
        errores.extend(_errores_de_busqueda(b, indice))
        errores.extend(errores_de_portales(b, indice))
        nombre = b.get("nombre") if isinstance(b, dict) else None
        if isinstance(nombre, str) and nombre.strip():
            clave = nombre.strip().casefold()
            if clave in vistos:
                errores.append(f"«{nombre.strip()}»: el nombre está repetido (también es la búsqueda {vistos[clave]}).")
            else:
                vistos[clave] = indice
    return errores


# ------------------------------------------------------------------ lectura

def cargar_busquedas() -> List[Dict[str, Any]]:
    """Las búsquedas válidas del candidato, con su consulta armada: [{nombre, activa, consulta}].

    Sin archivo, con el archivo roto o con el formato viejo, ninguna. Las inválidas se saltean (y se avisa en el
    log) para que una búsqueda mal escrita no frene a las demás."""
    ruta = core.paths.DATA_DIR / "candidato" / ARCHIVO
    try:
        if not ruta.is_file():
            return []
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("[Radar] No se pudo leer %s: %s. No hay búsquedas.", ruta, exc)
        return []
    errores = validar_busquedas(datos)
    if errores and not (isinstance(datos, dict) and isinstance(datos.get("busquedas"), list)):
        logger.error("[Radar] %s: %s No hay búsquedas.", ruta, errores[0])
        return []
    resultado: List[Dict[str, Any]] = []
    vistos = set()
    for indice, b in enumerate(datos["busquedas"], start=1):
        propios = _errores_de_busqueda(b, indice)
        if propios:
            logger.error("[Radar] Búsqueda salteada: %s", " ".join(propios))
            continue
        clave = b["nombre"].strip().casefold()
        if clave in vistos:
            logger.error("[Radar] Búsqueda salteada: «%s» tiene el nombre repetido.", b["nombre"].strip())
            continue
        vistos.add(clave)
        resultado.append({"nombre": b["nombre"].strip(), "activa": b["activa"], "consulta": armar_consulta(b)})
    return resultado


def _leer_archivo() -> Optional[Dict[str, Any]]:
    ruta = core.paths.DATA_DIR / "candidato" / ARCHIVO
    try:
        if not ruta.is_file():
            return None
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("[Portales] No se pudo leer %s: %s", ruta, exc)
        return None
    return datos if isinstance(datos, dict) and isinstance(datos.get("busquedas"), list) else None


def portales_de_busquedas() -> Dict[str, Dict[str, Any]]:
    """{nombre: {portales, palabras}} de cada búsqueda del archivo (para Mi perfil)."""
    datos = _leer_archivo() or {"busquedas": []}
    salida: Dict[str, Dict[str, Any]] = {}
    for b in datos["busquedas"]:
        if isinstance(b, dict) and isinstance(b.get("nombre"), str) and b["nombre"].strip():
            portales = b.get("portales") if isinstance(b.get("portales"), list) else []
            palabras = b.get("palabras") if isinstance(b.get("palabras"), str) else ""
            salida[b["nombre"].strip()] = {"portales": [p for p in portales if p in PORTALES], "palabras": palabras}
    return salida


def busquedas_de_portal(portal: str) -> List[Dict[str, str]]:
    """[{nombre, palabras}] de las búsquedas activas con `portal` marcado y palabras válidas, en el orden del archivo.
    Las que tienen un error en esos campos se saltean (y se avisa en el log)."""
    datos = _leer_archivo()
    if datos is None:
        return []
    salida: List[Dict[str, str]] = []
    vistos = set()
    for indice, b in enumerate(datos["busquedas"], start=1):
        if not isinstance(b, dict) or b.get("activa") is not True:
            continue
        nombre = b.get("nombre")
        if not isinstance(nombre, str) or not nombre.strip() or nombre.strip().casefold() in vistos:
            continue
        vistos.add(nombre.strip().casefold())
        if portal not in (b.get("portales") if isinstance(b.get("portales"), list) else []):
            continue
        errores = errores_de_portales(b, indice)
        if errores:
            logger.error("[Portales] Búsqueda salteada: %s", " ".join(errores))
            continue
        salida.append({"nombre": nombre.strip(), "palabras": _limpio(b["palabras"])})
    return salida


def errores_del_archivo() -> List[str]:
    """Los problemas de busquedas.json, para mostrarlos (sin archivo, ninguno): que una búsqueda mal escrita no
    desaparezca sin aviso."""
    ruta = core.paths.DATA_DIR / "candidato" / ARCHIVO
    try:
        if not ruta.is_file():
            return []
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"No se pudo leer {ARCHIVO}: {exc}"]
    return validar_busquedas(datos)


def consultas_activas() -> List[str]:
    """Las consultas armadas de las búsquedas activas, sin repetir y en el orden del archivo, que es la prioridad
    del usuario (las palabras clave ya marcan el idioma: creador, 12/11)."""
    consultas: List[str] = []
    for b in cargar_busquedas():
        if b["activa"] and b["consulta"] not in consultas:
            consultas.append(b["consulta"])
    return consultas


def nombre_de_consulta(consulta: str) -> str:
    """El nombre que el usuario le puso a la búsqueda que arma `consulta` («» si no está en el archivo)."""
    for b in cargar_busquedas():
        if b["consulta"] == consulta:
            return b["nombre"]
    return ""
