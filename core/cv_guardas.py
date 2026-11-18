"""Controles sin IA del perfil del CV (creador, 08/11).

El redactor escribía «más de doce años en monitoreo» cuando el perfil del candidato dice otra cifra, y copiaba
requisitos de la oferta («Residencia CABA o Primer Cordón») como si fueran datos propios. Esto corrige el
CV ya escrito, sin depender de que el modelo obedezca:

- Toda cifra de años del PERFIL tiene que ser una de las del perfil del candidato (`anios_por_area`). Si
  no lo es, se reemplaza por la cifra del área que nombra la frase («años en monitoreo» → monitoreo/ITSM);
  si la frase no nombra un área conocida, por la experiencia total.
- Las oraciones del PERFIL que repiten requisitos de la oferta sobre residencia, zona o modalidad se
  quitan: la ubicación real ya está en la línea de contacto.
"""
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import DATA_DIR
from core.idioma import seccion_canonica

RUTA_CONOCIMIENTOS_AUSENTES = DATA_DIR / "candidato" / "conocimientos_ausentes.json"

_NUMEROS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "twenty": 20,
}
_PAL_NUM = "|".join(sorted(_NUMEROS, key=len, reverse=True))
# «más de doce años», «+7 años», «7 años», «over 12 years», «12+ years»
_RE_ANIOS = re.compile(
    rf"(?P<pre>(?:m[aá]s de|over|more than)\s+|\+)?(?P<n>\d{{1,2}}|{_PAL_NUM})(?P<mas>\+)?\s+(?P<u>a[nñ]os?|years?)\b",
    re.IGNORECASE,
)
# Requisitos de la oferta copiados al perfil (residencia, zona, modalidad)
_RE_REQUISITO_COPIADO = re.compile(
    r"\b(?:residencia|residir|radicad[oa]|primer cord[oó]n|segundo cord[oó]n|zona (?:norte|sur|oeste)|"
    r"disponibilidad para (?:esquema|modalidad)|modalidad (?:h[ií]brida|presencial|remota)|"
    r"esquema h[ií]brido|relocat\w*|based in|residing|o afines|or related)\b",
    re.IGNORECASE,
)
_RE_ORACION = re.compile(r"[^.!?]+[.!?]*")
# Requisitos de logística o datos personales: no son experiencia, no van al CV ni cuentan como keyword ATS
_RE_REQUISITO_LOGISTICO = re.compile(
    r"\b(?:residencia|residir|radicaci\w*|ubicaci\w*|zona|cord[oó]n|modalidad|presencial|h[ií]brid\w*|remot\w*|"
    r"jornada|horario|turno|full ?time|part ?time|disponibilidad|movilidad|veh[ií]culo|edad|"
    r"relocat\w*|on ?site|location|schedule|availability|"
    r"incorporaci\w*|inicio inmediato|fecha de inicio|lunes a viernes|zona horaria|start date|timezone|"
    # Cómo postularse no es un requisito: «Envío de CV con pretensiones salariales»
    r"env[ií]o de (?:cv|curr[ií]culum)|enviar (?:el |tu )?cv|pretensi[oó]n\w*|salari\w*|remuneraci\w*)\b",
    re.IGNORECASE,
)


def es_requisito_logistico(nombre: str) -> bool:
    """«Residencia en CABA o Primer Cordón», «Jornada completa», «Modalidad híbrida»…"""
    return bool(_RE_REQUISITO_LOGISTICO.search(nombre or ""))


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower()) if unicodedata.category(c) != "Mn")


def cargar_conocimientos_ausentes(ruta: Optional[Path] = None) -> List[str]:
    """Carga los términos de conocimientos que el candidato no tiene."""
    p = ruta if ruta is not None else RUTA_CONOCIMIENTOS_AUSENTES
    try:
        p = Path(p)
        if not p.is_file():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        conocimientos = data.get("conocimientos")
        if not isinstance(conocimientos, list):
            return []
        return [str(x).strip() for x in conocimientos if isinstance(x, str) and str(x).strip()]
    except Exception:
        return []


def es_conocimiento_ausente(texto: str, ausentes: Optional[List[str]] = None) -> bool:
    """True si algún término de conocimientos ausentes aparece como palabra completa."""
    if not texto:
        return False
    if ausentes is None:
        ausentes = cargar_conocimientos_ausentes()
    if not ausentes:
        return False
    t = _sin_tildes(texto)
    for term in ausentes:
        if not term or not term.strip():
            continue
        term_norm = _sin_tildes(term.strip())
        if re.search(rf"\b{re.escape(term_norm)}\b", t, re.IGNORECASE):
            return True
    return False


def _numero(texto: str) -> Optional[int]:
    t = _sin_tildes(texto)
    if t.isdigit():
        return int(t)
    return _NUMEROS.get(t)


def _cifra_de(anios: Any) -> Optional[int]:
    m = re.search(r"\d+", str(anios or ""))
    return int(m.group()) if m else None


def _palabras(texto: str) -> set:
    return {p for p in re.split(r"[^a-z0-9]+", _sin_tildes(texto)) if len(p) > 3}


def _area_para(contexto: str, areas: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """El área que nombra la frase («años en monitoreo de infraestructura» → monitoreo/ITSM)."""
    orden = [p for p in re.split(r"[^a-z0-9]+", _sin_tildes(contexto)) if len(p) > 3]
    mejor, clave_mejor = None, None
    for a in areas:
        propias = _palabras(str(a.get("area")))
        posiciones = [i for i, p in enumerate(orden) if p in propias]
        if not posiciones:
            continue
        # Más palabras en común y, a igualdad, el área que se nombra primero
        clave = (len(set(orden) & propias), -posiciones[0])
        if clave_mejor is None or clave > clave_mejor:
            mejor, clave_mejor = a, clave
    return mejor


def _total(areas: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for a in areas:
        if "total" in _sin_tildes(str(a.get("area"))):
            return a
    return max(areas, key=lambda a: _cifra_de(a.get("anios")) or 0) if areas else None


def _texto_anios(area: Dict[str, Any], ingles: bool, mayuscula: bool) -> str:
    n = _cifra_de(area.get("anios"))
    mas = "mas de" in _sin_tildes(str(area.get("anios")))
    if ingles:
        texto = f"over {n} {'year' if n == 1 else 'years'}" if mas else f"{n} {'year' if n == 1 else 'years'}"
    else:
        texto = f"más de {n} {'año' if n == 1 else 'años'}" if mas else f"{n} {'año' if n == 1 else 'años'}"
    return texto[0].upper() + texto[1:] if mayuscula else texto


def corregir_anios(linea: str, areas: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """Reemplaza las cifras de años que no están en el perfil del candidato. Devuelve (línea, cambios)."""
    permitidas = {_cifra_de(a.get("anios")) for a in areas} - {None}
    if not permitidas:
        return linea, []
    cambios: List[str] = []

    def reemplazo(m: re.Match) -> str:
        n = _numero(m.group("n"))
        if n is None:
            return m.group(0)
        # El área es la que se nombra justo después («… años en monitoreo de …»), hasta la coma o el punto
        contexto = re.split(r"[,.;:]", linea[m.end():m.end() + 80], maxsplit=1)[0]
        area = _area_para(contexto, [a for a in areas if "total" not in _sin_tildes(str(a.get("area")))])
        if area is not None:
            if n == _cifra_de(area.get("anios")):
                return m.group(0)
        elif n in permitidas:
            return m.group(0)
        else:
            area = _total(areas)
        if area is None:
            return m.group(0)
        nuevo = _texto_anios(area, m.group("u").lower().startswith("year"), m.group(0)[:1].isupper())
        cambios.append(f"«{m.group(0)}» → «{nuevo}»")
        return nuevo

    return _RE_ANIOS.sub(reemplazo, linea), cambios


def quitar_requisitos_copiados(linea: str) -> Tuple[str, List[str]]:
    oraciones = _RE_ORACION.findall(linea)
    quedan = [o for o in oraciones if not _RE_REQUISITO_COPIADO.search(o)]
    if len(quedan) == len(oraciones) or not quedan:
        return linea, []
    quitadas = [o.strip() for o in oraciones if _RE_REQUISITO_COPIADO.search(o)]
    return " ".join(o.strip() for o in quedan), [f"se quitó «{q}»" for q in quitadas]


# Requisitos de la oferta que no son habilidades: estudios, títulos, logística (09/11: «Estudios en
# Sistemas o afines» aparecía como herramienta en HABILIDADES TÉCNICAS)
_RE_NO_ES_HABILIDAD = re.compile(
    r"\b(?:estudios?|t[ií]tulo|graduad[oa]|carrera|universitari[oa]|afines|degree|related field|"
    r"formaci[oó]n (?:en|universitaria|terciaria|acad[eé]mica)|"
    # El rubro no es una habilidad (09/11: «Mercado de capitales o fintech» en HABILIDADES TÉCNICAS)
    r"mercado de|fintech|industria|rubro|sector|"
    # Años o experiencia previa tampoco («Experiencia previa mayor a 3 años»)
    r"a[ñn]os|experiencia previa|years)\b",
    re.IGNORECASE,
)


def es_requisito_no_habilidad(nombre: str) -> bool:
    """Rubro, formación, años o logística: requisitos que nunca son una habilidad del CV (creador, 09/11)."""
    return bool(_RE_NO_ES_HABILIDAD.search(nombre or "")) or es_requisito_logistico(nombre) or es_conocimiento_ausente(nombre)


def quitar_requisitos_de_habilidades(cv_md: str) -> Tuple[str, List[str]]:
    """Saca de las viñetas de HABILIDADES los ítems que son requisitos de la oferta y no habilidades."""
    lineas = (cv_md or "").splitlines()
    en_hab = False
    quitados: List[str] = []
    ausentes = cargar_conocimientos_ausentes()
    for i, linea in enumerate(lineas):
        if linea.startswith("## "):
            en_hab = seccion_canonica(linea) == "HABILIDADES"
            continue
        m = re.match(r"^(\s*[•*\-]\s+\*\*[^*]+\*\*\s*)(.+?)(\.?)\s*$", linea) if en_hab else None
        if not m:
            continue
        items = [x.strip() for x in m.group(2).split(",")]
        malos = [
            x for x in items
            if _RE_NO_ES_HABILIDAD.search(x)
            or _RE_REQUISITO_LOGISTICO.search(x)
            or es_conocimiento_ausente(x, ausentes)
        ]
        quedan = [x for x in items if x not in malos]
        if malos and quedan:
            lineas[i] = f"{m.group(1)}{', '.join(quedan)}{m.group(3)}"
            quitados += malos
        elif malos and not quedan and any(es_conocimiento_ausente(x, ausentes) for x in malos):
            lineas[i] = None
            quitados += malos
    if not quitados:
        return cv_md, []
    lineas_finales = [l for l in lineas if l is not None]
    return ("\n".join(lineas_finales) + ("\n" if (cv_md or "").endswith("\n") else ""),
            [f"se quitó de habilidades «{q}»" for q in quitados])


def corregir_perfil(cv_md: str, areas: Optional[List[Dict[str, Any]]] = None) -> Tuple[str, List[str]]:
    """Aplica los controles a las líneas de texto de la sección PERFIL/SUMMARY. Devuelve (cv, cambios)."""
    if areas is None:
        from core.perfil_areas import anios_por_area_en_cache
        areas = anios_por_area_en_cache()
    lineas = (cv_md or "").splitlines()
    en_perfil = False
    cambios: List[str] = []
    for i, linea in enumerate(lineas):
        if linea.startswith("#"):
            en_perfil = linea.startswith("## ") and seccion_canonica(linea) == "PERFIL"
            continue
        if not en_perfil or not linea.strip():
            continue
        nueva, c1 = corregir_anios(linea, areas or [])
        nueva, c2 = quitar_requisitos_copiados(nueva)
        if nueva != linea:
            lineas[i] = nueva
            cambios += c1 + c2
    if not cambios:
        return cv_md, []
    return "\n".join(lineas) + ("\n" if (cv_md or "").endswith("\n") else ""), cambios


def bloque_prompt(areas: Optional[List[Dict[str, Any]]] = None) -> str:
    """Reglas de años y ubicación para el prompt del redactor del CV."""
    if areas is None:
        from core.perfil_areas import anios_por_area_en_cache
        areas = anios_por_area_en_cache()
    cifras = "; ".join(f"{a.get('area')}: {a.get('anios')} {'año' if _cifra_de(a.get('anios')) == 1 and 'mas de' not in _sin_tildes(str(a.get('anios'))) else 'años'}"
                       for a in areas) if areas else ""
    reglas = [
        "AÑOS Y UBICACIÓN EN EL PERFIL (obligatorio):",
        "- No copies requisitos de la oferta como datos del candidato (residencia, zona, «primer cordón», "
        "modalidad, disponibilidad para un esquema). La ubicación real ya está en la línea de contacto.",
    ]
    if cifras:
        reglas.insert(1, f"- Toda cantidad de años sale SOLO de estas cifras del candidato, la del área de la que "
                         f"hablás («más de 7» se escribe «más de 7 años»): {cifras}. No sumes ni redondees; "
                         f"si el área no figura, usá la experiencia total o no pongas cifra.")
    return "\n".join(reglas)


# ---------------------------------------------------------------------------
# Veracidad de los encabezados de EXPERIENCIA (creador, 09/11)
# La base del candidato (`experiencia.txt`) es la verdad: los títulos de rol, las empresas y las fechas
# se copian de ahí; el redactor solo elige qué contar de cada rol. En el CV completo va un `###` por
# rol; en el de 1 página, uno por empresa con un título real de esa empresa y el rango completo.
# ---------------------------------------------------------------------------

COMPLETO = "completo"
UNA_PAGINA = "una_pagina"
FORMATOS_CV = (COMPLETO, UNA_PAGINA)

_RE_RANGO_ENCABEZADO = re.compile(
    r"((?:\d{2}/)?\d{4})\s*(?:[-–—]|\ba\b)\s*((?:\d{2}/)?\d{4}|presente|actualidad|present|current)",
    re.IGNORECASE,
)


def normalizar_formato(formato: Optional[str]) -> str:
    return formato if formato in FORMATOS_CV else COMPLETO


def _norm(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _sin_tildes(texto)).strip()


def _anio(valor: str) -> int:
    m = re.search(r"\d{4}", valor or "")
    return int(m.group(0)) if m else 9999  # Presente / actualidad


def _rango(rol: Dict[str, Any]) -> Tuple[int, int]:
    return _anio(rol.get("desde", "")), _anio(rol.get("hasta", ""))


def encabezado_canonico(rol: str, empresa: str, desde: str = "", hasta: str = "") -> str:
    fechas = f" ({desde} - {hasta})" if desde and hasta else ""
    return f"### {rol} | {empresa}{fechas}"


def roles_por_empresa(roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Una entrada por empresa, en el orden de la base: títulos reales y rango completo de fechas."""
    grupos: Dict[str, Dict[str, Any]] = {}
    for r in roles:
        g = grupos.setdefault(r["empresa"], {"empresa": r["empresa"], "titulos": [], "roles": []})
        g["titulos"].append(r["rol"])
        g["roles"].append(r)
    for g in grupos.values():
        con_fechas = [r for r in g["roles"] if r.get("desde")]
        g["desde"] = min(con_fechas, key=lambda r: _rango(r)[0])["desde"] if con_fechas else ""
        g["hasta"] = max(con_fechas, key=lambda r: _rango(r)[1])["hasta"] if con_fechas else ""
    return list(grupos.values())


def _partes_encabezado(linea: str) -> Tuple[str, str, Optional[Tuple[int, int]]]:
    contenido = linea.lstrip("#").strip().replace("**", "")
    titulo = contenido.split("|")[0].strip()
    m = _RE_RANGO_ENCABEZADO.search(contenido)
    return titulo, _norm(contenido), ((_anio(m.group(1)), _anio(m.group(2))) if m else None)


def corregir_encabezados(cv_md: str, roles: List[Dict[str, Any]], formato: str = COMPLETO,
                         ingles: bool = False) -> Tuple[str, List[str], List[str]]:
    """Reescribe cada `###` de EXPERIENCIA con el rol, la empresa y las fechas de la base.

    Devuelve (cv, cambios, problemas). `problemas` son roles (o empresas, en 1 página) que faltan y
    encabezados sin respaldo en la base: bloquean la aprobación. En inglés se conserva el título
    traducido y solo se corrigen empresa y fechas.
    """
    if not roles:
        return cv_md, [], []
    formato = normalizar_formato(formato)
    unidades = roles_por_empresa(roles) if formato == UNA_PAGINA else [
        {**r, "titulos": [r["rol"]]} for r in roles]
    usadas = set()
    lineas = (cv_md or "").splitlines()
    en_exp = False
    cambios: List[str] = []
    problemas: List[str] = []
    for i, linea in enumerate(lineas):
        if linea.startswith("## "):
            en_exp = seccion_canonica(linea) == "EXPERIENCIA"
            continue
        if not en_exp or not linea.startswith("### "):
            continue
        titulo, resto, fechas = _partes_encabezado(linea)
        t_norm = _norm(titulo)

        def puntaje(k: int) -> int:
            u = unidades[k]
            p = 0
            if _norm(u["empresa"]) and _norm(u["empresa"]) in resto:
                p += 4
            if fechas and fechas == _rango(u):
                p += 2
            if any(_norm(t) == t_norm for t in u["titulos"]):
                p += 3
            return p

        libres = [k for k in range(len(unidades)) if k not in usadas]
        mejor = max(libres, key=puntaje, default=None)
        # Hace falta la empresa (4) o, sin ella, título y fechas a la vez (5)
        if mejor is None or puntaje(mejor) < 4:
            problemas.append(f"Encabezado sin respaldo en la base del candidato: «{linea.strip()}»")
            continue
        usadas.add(mejor)
        u = unidades[mejor]
        if ingles:
            nuevo_titulo = titulo
        elif any(_norm(t) == t_norm for t in u["titulos"]):
            nuevo_titulo = next(t for t in u["titulos"] if _norm(t) == t_norm)
        else:
            nuevo_titulo = u["titulos"][0]
        # La empresa escrita con otras mayúsculas («Freelance» / «FREELANCE») se respeta
        en_linea = re.search(re.escape(u["empresa"]), linea, flags=re.IGNORECASE)
        empresa_txt = en_linea.group(0) if en_linea else u["empresa"]
        nuevo = encabezado_canonico(nuevo_titulo, empresa_txt, u.get("desde", ""), u.get("hasta", ""))
        # Mismo contenido con otra puntuación, mayúsculas o tildes: queda como lo escribió el redactor
        if _norm(nuevo) != _norm(linea):
            cambios.append(f"«{linea.strip()}» → «{nuevo}»")
            lineas[i] = nuevo
    for k, u in enumerate(unidades):
        if k not in usadas:
            nombre = u["empresa"] if formato == UNA_PAGINA else f"{u['rol']} | {u['empresa']}"
            problemas.append(f"Falta en EXPERIENCIA: {nombre}")
    if not cambios:
        return cv_md, [], problemas
    return "\n".join(lineas) + ("\n" if (cv_md or "").endswith("\n") else ""), cambios, problemas
