"""CV desde el maestro: rápido y a la primera.

1. **Selección por código** (`seleccionar`): del CV maestro (`experiencia.txt`, completo, nunca recortado) se eligen
   las viñetas según el análisis y el matching dinámico (match y transferible). Todas las empresas quedan, con un
   encabezado por empresa (el rol más afín y el rango completo). Se llena 1 página.
2. **Ajuste** (`FlujoCV.ajustar`): una llamada a la IA con las líneas numeradas, la oferta y las pautas de
   `core/pautas_cv.py`; devuelve JSON por línea.
3. **Auditoría independiente** (`FlujoCV.auditar`): el auditor, el control de inventos y los largos. El pedido no la ve.
4. **Una sola corrección** con las líneas que fallaron; si no alcanza, ticket y `CVNoAprobado` (el post queda en ERROR).
5. **1 página** confirmada con el PDF de la plantilla real (`FlujoCV.paginas`)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from core import estructura_cv, pautas_cv
from core.cv_guardas import encabezado_canonico, es_requisito_logistico, es_requisito_no_habilidad, roles_por_empresa
from core.idioma import EN, ES, TITULOS
from core.profile_parser import parse_candidate_profile, parse_roles
from core.prompts_evaluacion import REGLA_DATOS_DE_TERCEROS, delimitar_datos_de_terceros

logger = logging.getLogger(__name__)

# Estimación de la plantilla (A4, márgenes de 15 mm, 9,5 pt), calibrada con Edge el 14/11: el PDF real confirma la página
RENGLONES_PAGINA = 56
CARACTERES_POR_RENGLON = 108
RENGLONES_TITULO_SECCION = 2.0
RENGLONES_ENCABEZADO_EMPRESA = 1.2
RENGLONES_CABECERA = 3.5
# Habilidades técnicas: 2 o 3 líneas, con lo que matchea la oferta y algunas afines
MAX_LINEAS_HABILIDADES = 3
MIN_LINEAS_HABILIDADES = 2  # para entrar en la página se sacan las que menos keywords aportan, hasta 2
AFINES_POR_LINEA = 2  # ítems del mismo grupo que no matchean pero acompañan
ITEMS_LINEA_SIN_MATCH = 3  # una línea que solo completa el mínimo lleva sus ítems más afines
# Pesos del puntaje de una viñeta: términos del matching dinámico y del resto del análisis
PESO_MATCH = 3.0
PESO_TRANSFERIBLE = 2.0
PESO_CONTEXTO = 1.0
# Compilaciones del PDF para ajustar la página (cada una tarda unos segundos)
MAX_COMPILACIONES = 6
CORRECCIONES = 1
CORRECCIONES_MECANICAS = 1  # una más si solo quedan controles por código nuevos (ver FlujoCV._mecanica_pendiente)
# Viñetas por cargo según su relevancia para la oferta: 1 solo en un cargo que no suma; un cargo
# relevante lleva al menos VINETAS_MIN_RELEVANTE y el lugar libre va a las viñetas que más demuestran la oferta
VINETAS_NO_RELEVANTE = 1
VINETAS_MIN_RELEVANTE = 2
# Selección con IA (es interpretación de texto): puntaje de 0 a 10 por viñeta y por ítem de
# habilidades según cuánto demuestra lo que pide la oferta. Si falla, la selección por palabras y un ticket
UMBRAL_CARGO_IA = 6  # un cargo suma si su mejor viñeta llega
UMBRAL_VINETA_IA = 5  # el lugar libre se llena solo con viñetas desde este puntaje
UMBRAL_ITEM_IA = 5  # un ítem de habilidades matchea desde este puntaje
MIN_PUNTUADAS_IA = 0.8  # la respuesta tiene que puntuar al menos esta parte de los ids
TIMEOUT_SELECCION_S = 90
EFFORT_SELECCION = "low"
# Educación: los estudios terciarios van siempre; un curso entra solo si sus temas están en lo que pide la oferta
# (match o transferible); el secundario sale cuando hay terciarios
_RE_SECUNDARIO = re.compile(r"\b(?:bachiller|secundari[oa]|polimodal|high school|secondary school)\b", re.IGNORECASE)
_RE_CURSO = re.compile(r"\b(?:curso|course|taller|workshop|seminario|seminar|bootcamp|certificaci[oó]n|certification)\b",
                       re.IGNORECASE)
_GENERICAS_CURSO = {"curso", "cours", "inten", "avanz", "advan", "intro", "basic", "básic", "admin", "taller", "works",
                    "semin", "certi", "nivel", "level", "boot"}
# Con "medium" el ajuste tardó 207 s y la corrección pasó los 240 s: el objetivo es CV y mail en 3 min
TIMEOUT_AJUSTE_S = 150
EFFORT_AJUSTE = "low"
# La traducción del maestro es una sola vez y larga
TIMEOUT_TRADUCCION_S = 600
EFFORT_TRADUCCION = "medium"
MAESTRO_EN = "experiencia_en.txt"
# Texto de relleno con el largo máximo del perfil, para reservar su lugar al llenar la página
PERFIL_RESERVADO = ("texto " * (pautas_cv.LARGO_MAX_PERFIL // 6)).strip()
MARCA_PENDIENTE = "<!-- PENDIENTE DE APROBACIÓN: traducción automática del CV maestro; revisala en Mi perfil -->"

_STOP = set("""de del la las el los y o en con para por que se un una al a su sus como entre sobre desde hasta cada
según mas más sin the and of for in with to on or at by an from as is are be into using via""".split())
_TITULOS_SECCION = {t.casefold() for idioma in TITULOS.values() for t in idioma.values()} | {
    "habilidades", "habilidades interpersonales", "habilidades blandas", "competencias", "soft skills",
    "interpersonal skills", "skills", "referencias", "references", "experiencia profesional", "professional experience"}


class CVNoAprobado(Exception):
    """El CV no pasó la auditoría ni con la única corrección: ya se abrió el ticket."""


@dataclass
class Linea:
    id: str
    texto: str
    original: str
    puntaje: float = 0.0
    depende: Optional[str] = None  # texto original de la viñeta anterior a la que remite («esas aplicaciones…»)
    orden: int = 0  # posición en el maestro, dentro de su empresa


# Una viñeta que arranca remitiendo a la anterior («Atendí el soporte de esas aplicaciones…») no se entiende sola
_RE_REMITE = re.compile(r"\b(?:es[ao]s?|est[ao]s?|dich[ao]s?|aquell[ao]s?|this|those|these|such|said)\b", re.IGNORECASE)
PALABRAS_REMISION = 8


# Una viñeta que sigue una etapa anterior del rol («Seguí con…, ahora con…», «Continued with…») no se entiende sola
_RE_CONTINUACION = re.compile(r"^(?:Segu[ií]|Continu[eé]|Continued|Kept on)\b|etapa anterior|previous (?:stage|phase|role)",
                              re.IGNORECASE)


def continua_otra_etapa(texto: str) -> bool:
    return bool(_RE_CONTINUACION.search(texto or ""))


def remite_a_la_anterior(texto: str) -> bool:
    """La viñeta usa un demostrativo en sus primeras palabras: su referente está en la viñeta anterior."""
    return bool(_RE_REMITE.search(" ".join((texto or "").split()[:PALABRAS_REMISION])))


@dataclass
class Bloque:
    """Un cargo real (o, con la regla «segun_oferta» del perfil, todos los cargos de la empresa bajo su título
    unificado) con sus viñetas: nunca viñetas de otro cargo."""
    empresa: str
    encabezado: str
    vinetas: List[Linea]
    reserva: List[Linea] = field(default_factory=list)
    minimo: int = 1  # viñetas que nunca se sacan para entrar en la página
    maximo: int = 0  # tope de viñetas del perfil (0 = sin tope)
    largo_max: int = 0  # tope de caracteres por viñeta del perfil («sintetizar»; 0 = el de las pautas)
    originales: Tuple[str, ...] = ()  # las viñetas del maestro que le pertenecen
    unificado: bool = False
    relevante: bool = True  # suma para la oferta; si no, lleva VINETAS_NO_RELEVANTE
    relevancia: float = 0.0  # el puntaje de su mejor viñeta: al recortar, el mínimo cede primero en el menos relevante

    def lleno(self) -> bool:
        return bool(self.maximo) and len(self.vinetas) >= self.maximo


@dataclass
class Hallazgo:
    linea: Optional[str]
    motivo: str
    senal: Optional[Dict[str, str]] = None  # de la revisión de estilo con IA: {"frase", "patron", "reescritura"}


@dataclass
class Borrador:
    nombre: str
    titulo: str
    contacto: str
    perfil: Linea
    experiencia: List[Bloque]
    habilidades: List[Linea]
    educacion: List[str]
    idiomas: List[str]
    idioma: str = ES
    areas: List[Dict[str, Any]] = field(default_factory=list)  # años por área del candidato (anios_por_area)
    # Selección con IA: cada término de la oferta → la viñeta del maestro que lo demuestra (solo ahí puede ir)
    evidencia: Dict[str, str] = field(default_factory=dict)
    seleccion_ia: bool = False
    # CV simple: la IA lo decide por oferta; el formato sale del perfil (estructura_cv.cv_simple)
    simple: bool = False
    titulo_habilidades: str = ""
    cargos_simples: Dict[str, str] = field(default_factory=dict)  # empresa → encabezado único del CV general

    def editables(self) -> List[Linea]:
        return [self.perfil] + [l for b in self.experiencia for l in b.vinetas] + self.habilidades

    def por_id(self, lid: str) -> Optional[Linea]:
        return next((l for l in self.editables() if l.id == lid), None)

    def markdown(self) -> str:
        t = TITULOS[self.idioma]
        for b in self.experiencia:  # el orden del maestro dentro de cada empresa
            b.vinetas.sort(key=lambda l: l.orden)
        partes = [f"# {self.nombre.upper()}"] + [x for x in (self.titulo, self.contacto) if x]
        partes += ["", f"## {t['PERFIL']}", self.perfil.texto, "", f"## {t['EXPERIENCIA']}"]
        for b in self.experiencia:
            partes += [b.encabezado] + [f"• {l.texto}" for l in b.vinetas] + [""]
        partes += [f"## {self.titulo_habilidades or t['HABILIDADES']}"] + [f"• {l.texto}" for l in self.habilidades]
        partes += ["", f"## {t['EDUCACION']}"] + [f"• {e}" for e in self.educacion]
        partes += ["", f"## {t['IDIOMAS']}"] + [f"• {i}" for i in self.idiomas]
        return "\n".join(partes).strip() + "\n"


# ---------------------------------------------------------------- términos del matching dinámico

def _norm(texto: str) -> str:
    sin = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return sin.casefold()


def _tokens(texto: str) -> Set[str]:
    """Raíces de 5 letras de las palabras con contenido (soporte/soportes, automatización/automaticé)."""
    palabras = re.findall(r"[a-z0-9#+.]+", _norm(texto))
    return {p.strip(".")[:5] for p in palabras if len(p.strip(".")) >= 3 and p.strip(".") not in _STOP}


# Requisitos blandos o de actitud (trato cordial, comunicación, compromiso): no son una práctica que se pueda
# demostrar en una viñeta; exigidos como keyword, la revisión de estilo los marca como autoelogio y el CV no se
# puede corregir (oferta 9993). Ni se exigen ni se incorporan.
_RE_REQUISITO_BLANDO = re.compile(
    r"\b(?:comunicaci[oó]n|trabajo en equipo|autonom[ií]a|proactiv|compromiso|actitud|responsabilidad|empat[ií]a"
    r"|trato cordial|cordialidad|orientaci[oó]n al (?:cliente|servicio|usuario)|resoluci[oó]n de problemas"
    r"|soft skills?|communication|teamwork|team player|proactive|commitment|attitude|empathy|problem[- ]solving"
    r"|customer (?:orientation|focus)|service orientation"
    # Valores y actitudes (10937, YPF): no son keywords ni van a la selección
    r"|excelencia|profesionalismo|foco en (?:el )?(?:usuario|cliente|valor)|visi[oó]n (?:integral|hol[ií]stica)"
    r"|impulso del cambio|mentalidad|pasi[oó]n por|cultura de|\bvalores\b)", re.IGNORECASE)


def es_requisito_blando(nombre: str) -> bool:
    return bool(_RE_REQUISITO_BLANDO.search(nombre or ""))


def _habilitado(r: Dict[str, Any]) -> bool:
    return (bool(r.get("approved", r.get("estado") == "MATCH_DIRECTO")) and r.get("estado") != "BRECHA_REAL"
            and not es_requisito_blando(str(r.get("nombre") or "")))


def terminos_del_matching(analisis: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(keywords obligatorias, términos habilitados). Obligatorias: lo que está en match y es una habilidad (sin
    lo logístico ni rubro, formación o años, como `server._keywords_del_cv`). Habilitados: match y transferible
    (por la IA o por el usuario en la tarjeta)."""
    reqs = [r for r in (analisis.get("requisitos") or []) if isinstance(r, dict) and r.get("nombre")]
    habilitados = [r["nombre"] for r in reqs if _habilitado(r)]
    obligatorias = [r["nombre"] for r in reqs if _habilitado(r) and r.get("estado") == "MATCH_DIRECTO"
                    and not r.get("logistico") and not es_requisito_logistico(r["nombre"])
                    and not es_requisito_no_habilidad(r["nombre"])]
    return obligatorias, habilitados


def _pesos(analisis: Dict[str, Any], contexto: bool = True) -> Dict[str, float]:
    pesos: Dict[str, float] = {}

    def sumar(texto: str, peso: float):
        for tok in _tokens(texto):
            pesos[tok] = max(pesos.get(tok, 0.0), peso)

    for d in analisis.get("dolores_empresa") or []:
        sumar(str(d), PESO_CONTEXTO)
    sumar(str(analisis.get("puesto") or ""), PESO_CONTEXTO)
    for r in analisis.get("requisitos") or []:
        if not isinstance(r, dict) or not _habilitado(r) or r.get("logistico"):
            continue
        peso = PESO_MATCH if r.get("estado") == "MATCH_DIRECTO" else PESO_TRANSFERIBLE
        if contexto:
            sumar(str(r.get("justificacion") or ""), PESO_CONTEXTO)
        sumar(" ".join(str(r.get(k) or "") for k in ("nombre", "texto", "equivalencia")), peso)
    return pesos


def _puntaje(texto: str, pesos: Dict[str, float]) -> float:
    return sum(pesos.get(t, 0.0) for t in _tokens(texto))


# ---------------------------------------------------------------- selección por código

def _secciones(maestro: str) -> Dict[str, List[str]]:
    """Viñetas de las secciones del maestro que no son empresas (habilidades, educación, idiomas)."""
    salida: Dict[str, List[str]] = {}
    actual = None
    for linea in (maestro or "").splitlines():
        s = linea.strip()
        if s.startswith("## ") and not s.startswith("### "):
            titulo = s[3:].strip().casefold()
            actual = titulo if titulo in _TITULOS_SECCION else None
            continue
        if actual and re.match(r"^[•*\-]\s+", s):
            salida.setdefault(actual, []).append(re.sub(r"^[•*\-]\s+", "", s))
    return salida


def _de_seccion(secciones: Dict[str, List[str]], *nombres: str) -> List[str]:
    return [v for n in nombres for v in secciones.get(n.casefold(), [])]


def _renglones(texto: str) -> float:
    return max(1, math.ceil(len(texto) / CARACTERES_POR_RENGLON))


def renglones_de(b: Borrador) -> float:
    total = RENGLONES_CABECERA + _renglones(b.contacto) + 5 * RENGLONES_TITULO_SECCION + _renglones(b.perfil.texto)
    total += sum(RENGLONES_ENCABEZADO_EMPRESA + sum(_renglones(l.texto) for l in bl.vinetas) for bl in b.experiencia)
    total += sum(_renglones(t) for t in [l.texto for l in b.habilidades] + b.educacion + b.idiomas)
    return total


def _anio(valor: str) -> int:
    m = re.search(r"\d{4}", valor or "")
    return int(m.group(0)) if m else time.localtime().tm_year  # Presente / actualidad


def seleccionar_educacion(items: List[str], analisis: Dict[str, Any]) -> List[str]:
    """Terciarios siempre; cursos solo si sus temas están en lo que pide la oferta; secundario solo sin terciarios."""
    pesos = {t: p for t, p in _pesos(analisis, contexto=False).items() if p >= PESO_TRANSFERIBLE}
    terciarios = [e for e in items if not _RE_SECUNDARIO.search(e) and not _RE_CURSO.search(e)]
    salida = []
    for e in items:
        if _RE_SECUNDARIO.search(e):
            if not terciarios:
                salida.append(e)
        elif _RE_CURSO.search(e):
            temas = _tokens(e.split(" - ")[0]) - _GENERICAS_CURSO
            if any(pesos.get(t) for t in temas):
                salida.append(e)
        else:
            salida.append(e)
    return salida


@dataclass
class PuntajesIA:
    """Puntajes de 0 a 10 de la etapa de selección, por texto de viñeta y por ítem de habilidades del maestro, y la
    evidencia de cada requisito: la línea del maestro que lo demuestra y el cambio exacto para
    nombrarlo con el término de la oferta. El código aplica esos cambios; nadie adivina dónde va un término."""
    vinetas: Dict[str, float]
    items: Dict[str, float]
    # Viñeta del maestro → [(reemplazar, por)]
    reemplazos: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    # Ítem de habilidades del maestro → términos a agregar en su línea
    agregados: Dict[str, List[str]] = field(default_factory=dict)
    # Requisitos (nombre del análisis) con una línea que los demuestra
    evidenciados: Set[str] = field(default_factory=set)
    # «simple» o «completo» (la IA decide por oferta) y por qué
    tipo_cv: str = "completo"
    motivo_tipo: str = ""
    # CV general con un cargo por empresa: empresa → el cargo que la IA nombra para la oferta (se valida al usarlo)
    cargos: Dict[str, str] = field(default_factory=dict)


class CriterioPalabras:
    """Relevancia por las palabras del análisis (el respaldo si la selección con IA falla). Un puntaje de viñeta
    mayor que 0 suma para la oferta."""

    def __init__(self, analisis: Dict[str, Any], roles: List[Dict[str, Any]], entre: int):
        self.pesos = _pesos(analisis)
        self.fuertes = {t: p for t, p in _pesos(analisis, contexto=False).items() if p >= PESO_TRANSFERIBLE}
        self.umbral = umbral_relevancia(roles, self.pesos, entre)

    def vineta(self, texto: str) -> float:
        return _puntaje(texto, self.pesos)

    def texto(self, vineta: str) -> str:
        return vineta

    def agregar(self, items: List[str]) -> List[str]:
        return []

    def suma(self, vinetas: List[str]) -> bool:
        return suma_para_la_oferta(vinetas, self.pesos, self.umbral)

    def matchea(self, item: str) -> bool:
        # La mayoría de sus palabras son de la oferta: «Linux» sí, «historias de usuario» no por pedir «usuarios»
        toks = _tokens(item)
        return bool(toks) and sum(t in self.fuertes for t in toks) / len(toks) > 0.5

    def peso_item(self, item: str) -> float:
        return _puntaje(item, self.fuertes)

    def afinidad(self, item: str) -> float:
        return _puntaje(item, self.pesos)

    def grupo(self, etiqueta: str) -> float:
        return _puntaje(etiqueta, self.fuertes)  # «Incidentes, ITSM…» suma para soporte aunque sus herramientas no


class CriterioIA:
    """Relevancia por los puntajes de la IA (interpreta lo que pide la oferta, no las palabras en común)."""

    def __init__(self, puntajes: PuntajesIA):
        self.p = puntajes

    def texto(self, vineta: str) -> str:
        """La viñeta con los términos de la oferta que la selección le asignó (reemplazo literal)."""
        for de, por in self.p.reemplazos.get(vineta, []):
            vineta = vineta.replace(de, por, 1)
        return vineta

    def agregar(self, items: List[str]) -> List[str]:
        """Términos de la oferta a sumar a la línea de habilidades que tiene esos ítems."""
        return list(dict.fromkeys(t for it in items for t in self.p.agregados.get(it, [])
                                  if t.casefold() not in {i.casefold() for i in items}))

    def vineta(self, texto: str) -> float:
        # Mayor que 0 = suma para la oferta (UMBRAL_VINETA_IA o más), como en el criterio por palabras
        return self.p.vinetas.get(texto, 0.0) - (UMBRAL_VINETA_IA - 0.5)

    def suma(self, vinetas: List[str]) -> bool:
        return max((self.p.vinetas.get(v, 0.0) for v in vinetas), default=0.0) >= UMBRAL_CARGO_IA

    def matchea(self, item: str) -> bool:
        return self.p.items.get(item, 0.0) >= UMBRAL_ITEM_IA

    def peso_item(self, item: str) -> float:
        return self.p.items.get(item, 0.0)

    def afinidad(self, item: str) -> float:
        return self.p.items.get(item, 0.0)

    def grupo(self, etiqueta: str) -> float:
        return 0.0


def _lineas_de(vinetas: List[str], criterio, desde: int = 0) -> List[Linea]:
    lineas = []
    for i, v in enumerate(vinetas):
        if continua_otra_etapa(v):
            continue  # nunca se elige: sola no se entiende
        previa = vinetas[i - 1] if i and remite_a_la_anterior(v) else None
        n = desde + len(lineas)
        lineas.append(Linea("", criterio.texto(v), v, criterio.vineta(v) - n * 1e-3, previa, n))
    return lineas


def _bloque(empresa: str, encabezado: str, lineas: List[Linea], originales: List[str],
            maximo: int = 0, largo_max: int = 0, unificado: bool = False, relevante: bool = True) -> Bloque:
    """Un cargo que no suma para la oferta lleva 1 viñeta (la de más puntaje); uno relevante arranca con su mínimo."""
    if not relevante:
        maximo = min(maximo, VINETAS_NO_RELEVANTE) if maximo else VINETAS_NO_RELEVANTE
    ordenadas = sorted(lineas, key=lambda l: (l.depende is not None, -l.puntaje))
    bloque = Bloque(empresa, encabezado, ordenadas[:1], sorted(ordenadas[1:], key=lambda l: -l.puntaje),
                    maximo=maximo, largo_max=largo_max, originales=tuple(originales), unificado=unificado,
                    relevante=relevante, relevancia=max((l.puntaje for l in lineas), default=0.0))
    if relevante:
        while len(bloque.vinetas) < VINETAS_MIN_RELEVANTE and bloque.reserva and not bloque.lleno():
            for l in _con_su_anterior(bloque.reserva[0], bloque):
                bloque.reserva.remove(l)
                bloque.vinetas.append(l)
        bloque.minimo = len(bloque.vinetas)
    return bloque


def umbral_relevancia(roles: List[Dict[str, Any]], pesos: Dict[str, float], entre: int) -> float:
    """El puntaje de la viñeta número `entre` del maestro para esta oferta: un cargo suma si alguna de sus viñetas
    llega."""
    puntajes = sorted((_puntaje(v, pesos) for r in roles for v in r["vinetas"]), reverse=True)
    return puntajes[min(entre, len(puntajes)) - 1] if puntajes else 0.0


def bloques_de_empresa(g: Dict[str, Any], criterio, regla: "estructura_cv.ReglaEmpresa",
                       ingles: bool) -> List[Bloque]:
    """Los bloques de una empresa según la regla del perfil: un bloque por cargo real, con su título y sus fechas;
    con «segun_oferta», si algún cargo no suma, un solo bloque con el título unificado y el período de la empresa.
    Un cargo (o el unificado) que no suma lleva 1 viñeta."""
    suman = [criterio.suma(r["vinetas"]) for r in g["roles"]]
    if regla.cargos == estructura_cv.SEGUN_OFERTA and len(g["roles"]) > 1 and not all(suman):
        todas = [v for r in g["roles"] for v in r["vinetas"]]
        enc = encabezado_canonico(regla.titulo(ingles), g["empresa"], g["desde"], g["hasta"])
        lineas: List[Linea] = []
        for r in g["roles"]:
            lineas += _lineas_de(r["vinetas"], criterio, len(lineas))
        return [_bloque(g["empresa"], enc, lineas, todas,
                        regla.vinetas_unificado or regla.vinetas_max_por_cargo, regla.largo_max_vineta, True,
                        relevante=any(suman))]
    return [_bloque(g["empresa"], encabezado_canonico(r["rol"], g["empresa"], r.get("desde", ""), r.get("hasta", "")),
                    _lineas_de(r["vinetas"], criterio), r["vinetas"], regla.vinetas_max_por_cargo, regla.largo_max_vineta, relevante=suma)
            for r, suma in zip(g["roles"], suman)]


def suma_para_la_oferta(vinetas: List[str], pesos: Dict[str, float], umbral: float) -> bool:
    """Un cargo suma si su mejor viñeta llega al umbral (y algo matchea: con un umbral de 0 no suma cualquiera)."""
    mejor = max((_puntaje(v, pesos) for v in vinetas), default=0.0)
    return mejor > 0 and mejor >= umbral


_RE_ETIQUETA_HABILIDAD = re.compile(r"^\*\*(.+?):?\*\*:?\s*")


def items_de_habilidad(texto: str) -> Tuple[str, List[str]]:
    """(etiqueta, ítems) de una línea de habilidades «**Grupo:** a, b (c, d), e.»: las comas entre paréntesis no
    separan ítems."""
    texto = (texto or "").strip()
    m = _RE_ETIQUETA_HABILIDAD.match(texto)
    etiqueta = m.group(1).strip().rstrip(":") if m else ""
    cuerpo = texto[m.end():] if m else texto
    cuerpo = cuerpo.rstrip().rstrip(".")
    items, actual, nivel = [], "", 0
    for c in cuerpo:
        nivel += (c == "(") - (c == ")")
        if c == "," and nivel <= 0:
            items.append(actual.strip())
            actual = ""
        else:
            actual += c
    items.append(actual.strip())
    return etiqueta, [i for i in items if i]


def _linea_de_habilidad(etiqueta: str, items: List[str]) -> str:
    return f"**{etiqueta}:** {', '.join(items)}." if etiqueta else f"{', '.join(items)}."


def seleccionar_habilidades(lineas: List[str], analisis: Dict[str, Any], criterio=None) -> List[str]:
    """Hasta MAX_LINEAS_HABILIDADES líneas del maestro, las que más matchean la oferta, cada una con sus ítems que
    matchean más hasta AFINES_POR_LINEA afines del mismo grupo, en el orden del maestro. Una línea
    sin ningún ítem que matchee no entra, salvo para llegar a MIN_LINEAS_HABILIDADES (con sus ítems más afines)."""
    criterio = criterio or CriterioPalabras(analisis, [], 1)
    candidatas = []
    for i, linea in enumerate(lineas):
        etiqueta, items = items_de_habilidad(linea)
        match = [it for it in items if criterio.matchea(it)]
        resto = sorted((it for it in items if it not in match),
                       key=lambda it: (-criterio.afinidad(it), items.index(it)))
        elegidos = set(match + resto[:AFINES_POR_LINEA]) if match else set(resto[:ITEMS_LINEA_SIN_MATCH])
        del_grupo = criterio.grupo(etiqueta)
        agregados = criterio.agregar(items)  # términos de la oferta que la selección asignó a este grupo
        clave = (sum(criterio.peso_item(it) for it in match) + del_grupo + 10 * len(agregados),
                 sum(criterio.afinidad(it) for it in items), -i)
        candidatas.append((clave, i, _linea_de_habilidad(etiqueta, [it for it in items if it in elegidos] + agregados),
                           bool(match) or del_grupo > 0 or bool(agregados)))
    candidatas.sort(key=lambda c: c[0], reverse=True)
    elegidas = [c for c in candidatas if c[3]][:MAX_LINEAS_HABILIDADES]
    elegidas += [c for c in candidatas if not c[3]][:max(0, MIN_LINEAS_HABILIDADES - len(elegidas))]
    return [c[2] for c in sorted(elegidas, key=lambda c: c[1])]


def seleccionar(maestro: str, personales: str, analisis: Dict[str, Any], *, idioma: str = ES, titulo: str = "",
                renglones: float = RENGLONES_PAGINA, areas: Optional[List[Dict[str, Any]]] = None,
                estructura: Optional["estructura_cv.Estructura"] = None,
                puntajes: Optional[PuntajesIA] = None) -> Borrador:
    """Borrador de 1 página con el texto del maestro sin cambios: cada cargo real con al menos una viñeta (según la
    estructura del perfil) y después las de más puntaje hasta llenar `renglones`. Con `puntajes` (la etapa de
    selección con IA) la relevancia es la de la IA; sin ellos, por las palabras del análisis."""
    estructura = estructura_cv.por_defecto(estructura)
    perfil_cand = parse_candidate_profile(maestro, personales)
    roles = parse_roles(maestro)
    secciones = _secciones(maestro)
    criterio = CriterioIA(puntajes) if puntajes else CriterioPalabras(analisis, roles,
                                                                       estructura.cargos_relevantes_entre)
    bloques: List[Bloque] = []
    for g in roles_por_empresa(roles):
        bloques += bloques_de_empresa(g, criterio, estructura.regla(g["empresa"]), idioma == EN)
    simple = estructura.cv_simple if puntajes and puntajes.tipo_cv == TIPO_SIMPLE else None
    cargos_simples: Dict[str, str] = {}
    if simple and simple.un_cargo_por_empresa:
        # CV general: un solo cargo por empresa, nombrado para la oferta, con el período de la empresa
        bloques = []
        for g in roles_por_empresa(roles):
            enc = encabezado_canonico(cargo_de_empresa(g, puntajes.cargos.get(g["empresa"], "")), g["empresa"],
                                      g["desde"], g["hasta"])
            cargos_simples[g["empresa"]] = enc
            lineas: List[Linea] = []
            for r in g["roles"]:
                lineas += _lineas_de(r["vinetas"], criterio, len(lineas))
            bloques.append(_bloque(g["empresa"], enc, lineas, [v for r in g["roles"] for v in r["vinetas"]],
                                   simple.vinetas_por_cargo, unificado=True))
    if simple:
        # CV simple: cada cargo con su mejor viñeta para el puesto (hasta `vinetas_por_cargo`)
        for b in bloques:
            b.maximo = simple.vinetas_por_cargo
            while len(b.vinetas) > b.maximo:
                peor = min(b.vinetas, key=lambda l: l.puntaje)
                b.vinetas.remove(peor)
                b.reserva.insert(0, peor)
            b.minimo = min(b.minimo, len(b.vinetas))
        lineas = _de_seccion(secciones, *simple.secciones_habilidades)
        mejores = sorted(lineas, key=lambda l: -puntajes.items.get(l, 0.0))[:simple.lineas_habilidades]
        habilidades = [l for l in lineas if l in mejores]
    else:
        habilidades = seleccionar_habilidades(_habilidades_del_maestro(maestro), analisis, criterio)
    educacion = seleccionar_educacion(_de_seccion(secciones, "education", "educación") or perfil_cand["educacion"],
                                      analisis)
    idiomas = _de_seccion(secciones, "languages", "idiomas") or perfil_cand["idiomas"]
    perfil = Linea("P1", "", "")
    borrador = Borrador(perfil_cand["nombre"] or "", titulo, perfil_cand["contacto"] or "", perfil, bloques,
                        [Linea("", h, h) for h in habilidades], educacion, idiomas, idioma, list(areas or []))
    if puntajes:
        borrador.seleccion_ia = True
        borrador.evidencia = {por: vineta for vineta, cambios in puntajes.reemplazos.items() for _, por in cambios}
    if simple:
        borrador.simple = True
        borrador.titulo_habilidades = simple.titulo(idioma == EN)
        borrador.cargos_simples = cargos_simples
        # Un puesto ajeno al perfil (depósito, comercio, limpieza): el perfil no menciona años
        borrador.areas = []
    # Perfil: se reservan sus renglones antes de llenar
    borrador.perfil.texto = "x" * (pautas_cv.LARGO_MAX_PERFIL - 40)
    while not (simple and not simple.llenar_pagina):
        # Solo lo que suma para la oferta: el lugar libre no se llena con viñetas que no matchean nada
        candidatas = [(l, b) for b in bloques if not b.lleno() for l in b.reserva if l.puntaje > 0]
        if not candidatas:
            break
        linea, bloque = max(candidatas, key=lambda c: c[0].puntaje)
        grupo = _con_su_anterior(linea, bloque)
        if renglones_de(borrador) + sum(_renglones(l.texto) for l in grupo) > renglones:
            break
        for l in grupo:
            bloque.reserva.remove(l)
            bloque.vinetas.append(l)
    borrador.perfil.texto = ""
    # Dentro de cada empresa, el orden del maestro
    for b in bloques:
        b.vinetas.sort(key=lambda l: _indice_original(l, roles, b.empresa))
    _numerar(borrador)
    return borrador


def _habilidades_del_maestro(maestro: str) -> List[str]:
    return _de_seccion(_secciones(maestro), "habilidades técnicas", "habilidades tecnicas", "technical skills",
                       "habilidades", "skills")


# ---------------------------------------------------------------- selección con IA

PEDIDO_SELECCION = "Puntuá cuánto demuestra cada línea del CV"
TIPO_SIMPLE, TIPO_COMPLETO = "simple", "completo"


def requisitos_ids(analisis: Dict[str, Any]) -> Dict[str, str]:
    """{R1: nombre}: los requisitos habilitados (match o transferible) del análisis, numerados."""
    nombres = [r["nombre"] for r in analisis.get("requisitos") or []
               if isinstance(r, dict) and r.get("nombre") and _habilitado(r)]
    return {f"R{i}": n for i, n in enumerate(dict.fromkeys(nombres), 1)}


def pedido_seleccion(maestro: str, analisis: Dict[str, Any], oferta: str, idioma: str = ES,
                     extra_habilidades: Tuple[str, ...] = (),
                     simple: Optional["estructura_cv.CVSimple"] = None) -> Tuple[str, Dict[str, str], Dict[str, str]]:
    """(prompt, {id: viñeta}, {id: ítem}): cada viñeta y cada ítem de habilidades del maestro con un id.
    `extra_habilidades`: líneas enteras de otras secciones (las del CV simple), cada una un ítem. Con `simple` (el
    CV general de Mi perfil, activo) la IA dice además el tipo de CV y, si va un cargo por empresa, cuál."""
    vinetas: Dict[str, str] = {}
    items: Dict[str, str] = {}
    lineas_e, lineas_h = [], []
    for r in parse_roles(maestro):
        lineas_e.append(f"[{r['rol']} | {r['empresa']} ({r.get('desde', '')} - {r.get('hasta', '')})]")
        for v in r["vinetas"]:
            vid = f"E{len(vinetas) + 1}"
            vinetas[vid] = v
            lineas_e.append(f"{vid}: {v}")
    for linea in _habilidades_del_maestro(maestro):
        etiqueta, its = items_de_habilidad(linea)
        lineas_h.append(f"[{etiqueta or 'Habilidades'}]")
        for it in its:
            iid = f"H{len(items) + 1}"
            items[iid] = it
            lineas_h.append(f"{iid}: {it}")
    if extra_habilidades:
        lineas_h.append("[Otras habilidades]")
        for linea in extra_habilidades:
            iid = f"H{len(items) + 1}"
            items[iid] = linea
            lineas_h.append(f"{iid}: {linea}")
    requisitos = "\n".join(f"{rid}: {nombre}" for rid, nombre in requisitos_ids(analisis).items())
    lengua = "inglés" if idioma == EN else "castellano"
    prompt = f"""{PEDIDO_SELECCION} del candidato lo que pide esta oferta. Con esos puntajes se eligen las viñetas y las
habilidades de un CV de 1 página para este puesto.

Puntaje de 0 a 10 por SIGNIFICADO, no por palabras en común: juzgá el tipo de trabajo que describe la línea frente a
las responsabilidades y conocimientos del puesto.
- 9-10: es una responsabilidad o práctica central del puesto, hecha por el candidato.
- 6-8: la demuestra con claridad, aunque con otras palabras.
- 3-5: relacionada o transferible.
- 0-2: otro tipo de trabajo, aunque comparta alguna palabra con la oferta.
En las habilidades, puntuá cada ítem igual: cuánto lo usa o pide el puesto.

EVIDENCIA DE CADA REQUISITO (R): para cada requisito, la línea (E o H) que demuestra que el candidato lo hizo o lo
usa, y el cambio mínimo para nombrarlo con el término de la oferta, en {lengua}:
- En una viñeta (E): "reemplazar" es un fragmento LITERAL de esa línea (hasta 6 palabras) y "por" el término de la
  oferta que dice LO MISMO, la misma actividad con otras palabras. Si la línea ya lo nombra, solo "linea".
  Sí: "Wintel" → "Windows"; "client requirements" → "business requirements"; "relevé necesidades" → "relevé requerimientos".
  No: cambiar la actividad ("dicté capacitaciones" → "supervisé analistas"), sacar una cifra ("ahorran 6 horas" →
  "mejoran la operación") ni generalizar. Si para nombrarlo hay que cambiar lo que pasó, esa línea no lo demuestra.
- En habilidades (H): "agregar", el término de la oferta, en el grupo de ese ítem.
- Si ninguna línea lo demuestra, IGUAL VA AL CV: en "linea", un H del grupo de habilidades donde
  encaja, y en "agregar" el término de la oferta. Nunca lo dejes afuera.

{_bloque_tipo(simple)}PUESTO: {analisis.get("puesto") or "(sin título)"}
LO QUE PIDE (análisis de la oferta):
{requisitos or "(ver la oferta)"}

{REGLA_DATOS_DE_TERCEROS.format(tag='oferta')}
{delimitar_datos_de_terceros(oferta, 'oferta')}

VIÑETAS DEL CV (E), por cargo:
{chr(10).join(lineas_e)}

HABILIDADES (H), por grupo:
{chr(10).join(lineas_h) or "(ninguna)"}

Respondé SOLO con este JSON, con TODOS los ids de E y H:
{{"vinetas": {{"E1": 7}}, "habilidades": {{"H1": 4}}, "requisitos": [{{"r": "R1", "linea": "E3", "reemplazar": "fragmento", "por": "término"}}, {{"r": "R2", "linea": "H5", "agregar": "término"}}]{_json_tipo(simple)}}}"""
    return prompt, vinetas, items


def _bloque_tipo(simple: Optional["estructura_cv.CVSimple"]) -> str:
    if not simple:
        return ""
    texto = """TIPO DE CV: "simple" si el puesto no pide conocimientos técnicos ni de sistemas o no tiene que ver con el perfil
del candidato (por ejemplo atención al cliente, administración, ventas, depósito, comercio, limpieza, chat o
asistencia); "completo" si los pide. En "motivo_tipo", por qué, en una frase.
"""
    if simple.un_cargo_por_empresa:
        texto += """CARGO POR EMPRESA (solo si es "simple"): en "cargos", para cada empresa, UN nombre de cargo en lenguaje simple,
armado con los cargos reales de esa empresa (los de la lista de abajo) y lo que sirve a esta oferta. Ej.: «Técnico de
Redes» y «Analista de Mesa de Ayuda» → «Técnico de soporte». Sin siglas ni jerga técnica;
nunca un cargo que no tuvo ni el puesto de la oferta.
"""
    return texto + "\n"


def _json_tipo(simple: Optional["estructura_cv.CVSimple"]) -> str:
    if not simple:
        return ""
    cargos = ', "cargos": {"Empresa": "cargo"}' if simple.un_cargo_por_empresa else ""
    return f', "tipo_cv": "completo", "motivo_tipo": "frase"{cargos}'


# Un cargo nombrado por la IA para el CV general: corto y con alguna palabra de los cargos reales de la empresa
LARGO_MAX_CARGO = 60


def cargo_de_empresa(g: Dict[str, Any], propuesto: str) -> str:
    """El cargo del bloque único de una empresa en el CV general: el que nombró la IA si sale de los cargos reales de
    esa empresa (comparte alguna palabra); si no, el cargo real más reciente."""
    propuesto = " ".join(str(propuesto or "").split())
    reales = " ".join(r["rol"] for r in g["roles"])
    if propuesto and len(propuesto) <= LARGO_MAX_CARGO and _tokens(propuesto) & _tokens(reales):
        return propuesto
    return max(g["roles"], key=lambda r: (_anio_de(r.get("hasta", "")), _anio_de(r.get("desde", ""))))["rol"]


def _anio_de(texto: str) -> int:
    m = re.search(r"\d{4}", str(texto or ""))
    return int(m.group()) if m else (9999 if re.search(r"actual|presente|present", str(texto or ""), re.I) else 0)


LARGO_MAX_TERMINO = 80
# Una viñeta que demuestra un requisito entra al CV con al menos este puntaje
PUNTAJE_EVIDENCIA = 8.0


def _evidencia(respuesta: Dict[str, Any], vinetas: Dict[str, str], items: Dict[str, str],
               requisitos: Dict[str, str], p: PuntajesIA) -> None:
    """Carga en `p` la evidencia de cada requisito. Solo vale lo verificable: la línea existe y, en una viñeta, el
    fragmento a reemplazar está tal cual en ella; lo demás se ignora (ese requisito queda sin evidencia)."""
    for e in respuesta.get("requisitos") or []:
        if not isinstance(e, dict) or str(e.get("r")) not in requisitos:
            continue
        linea, nombre = str(e.get("linea") or ""), requisitos[str(e.get("r"))]
        if linea in vinetas:
            texto = vinetas[linea]
            de, por = str(e.get("reemplazar") or "").strip(), str(e.get("por") or "").strip()
            if de or por:
                if not (de and por and de in texto and len(de) <= LARGO_MAX_TERMINO and len(por) <= LARGO_MAX_TERMINO):
                    continue
                # Un reemplazo no saca ni agrega cifras: «ahorran 6 horas» → «mejoran la operación» pierde el dato
                if set(_RE_CIFRA.findall(de)) != set(_RE_CIFRA.findall(por)):
                    continue
                if (de, por) not in p.reemplazos.setdefault(texto, []):
                    p.reemplazos[texto].append((de, por))
            p.vinetas[texto] = max(p.vinetas.get(texto, 0.0), PUNTAJE_EVIDENCIA)
            p.evidenciados.add(nombre)
        elif linea in items:
            termino = str(e.get("agregar") or "").strip()
            if termino and len(termino) <= LARGO_MAX_TERMINO:
                p.agregados.setdefault(items[linea], []).append(termino)
            p.evidenciados.add(nombre)


def puntajes_de(respuesta: Any, vinetas: Dict[str, str], items: Dict[str, str],
                analisis: Optional[Dict[str, Any]] = None) -> PuntajesIA:
    """Los puntajes de la respuesta, por texto, y la evidencia de cada requisito del `analisis`. Los ids desconocidos
    se ignoran; si falta más del 20 % de los ids o el formato no es el pedido, `ValueError`."""
    if isinstance(respuesta, str):
        respuesta = json.loads(respuesta)
    if not isinstance(respuesta, dict):
        raise ValueError("la selección no devolvió un JSON")

    def leer(dato: Any, ids: Dict[str, str]) -> Dict[str, float]:
        salida: Dict[str, float] = {}
        for k, v in (dato.items() if isinstance(dato, dict) else []):
            try:
                valor = float(v)
            except (TypeError, ValueError):
                continue
            if str(k) in ids and 0 <= valor <= 10:
                texto = ids[str(k)]
                salida[texto] = max(salida.get(texto, 0.0), valor)
        return salida

    pv, pi = leer(respuesta.get("vinetas"), vinetas), leer(respuesta.get("habilidades"), items)
    if len(pv) < MIN_PUNTUADAS_IA * len(set(vinetas.values())) or len(pi) < MIN_PUNTUADAS_IA * len(set(items.values())):
        raise ValueError(f"la selección puntuó {len(pv)} de {len(vinetas)} viñetas y {len(pi)} de {len(items)} ítems")
    tipo = str(respuesta.get("tipo_cv") or "").strip().lower()
    puntajes = PuntajesIA(pv, pi, tipo_cv=tipo if tipo in (TIPO_SIMPLE, TIPO_COMPLETO) else TIPO_COMPLETO,
                          motivo_tipo=str(respuesta.get("motivo_tipo") or "").strip()[:200])
    cargos = respuesta.get("cargos")
    if isinstance(cargos, dict):
        puntajes.cargos = {str(k).strip(): str(v).strip() for k, v in cargos.items() if isinstance(v, str)}
    _evidencia(respuesta, vinetas, items, requisitos_ids(analisis or {}), puntajes)
    return puntajes


def _con_su_anterior(linea: Linea, bloque: Bloque) -> List[Linea]:
    """La viñeta y, si remite a la anterior y esa no está en el CV, también la anterior."""
    if linea.depende and all(l.original != linea.depende for l in bloque.vinetas):
        previa = next((l for l in bloque.reserva if l.original == linea.depende), None)
        return [previa, linea] if previa else [linea]
    return [linea]


def _sostiene_a_otra(linea: Linea, bloque: Bloque) -> bool:
    return any(l.depende == linea.original for l in bloque.vinetas)


def _indice_original(linea: Linea, roles: List[Dict[str, Any]], empresa: str) -> int:
    vinetas = [v for r in roles if r["empresa"] == empresa for v in r["vinetas"]]
    return vinetas.index(linea.original) if linea.original in vinetas else len(vinetas)


def _numerar(b: Borrador) -> None:
    n = 0
    for bloque in b.experiencia:
        for l in bloque.vinetas + bloque.reserva:
            n += 1
            l.id = f"V{n}"
    for i, l in enumerate(b.habilidades, 1):
        l.id = f"S{i}"


# ---------------------------------------------------------------- ajuste: JSON por línea

def siglas_del_titulo(titulo: str, fuentes: str) -> Set[str]:
    """Palabras del título que son siglas (IT, QA, SAP): en la oferta o el maestro aparecen en mayúscula dentro de una
    línea con minúsculas (un título gritado todo en mayúscula no cuenta)."""
    siglas: Set[str] = set()
    for palabra in re.findall(r"[A-ZÁÉÍÓÚÑ]{2,5}", titulo or ""):
        patron = re.compile(rf"(?<![\wÁÉÍÓÚÑáéíóúñ]){re.escape(palabra)}(?![\wÁÉÍÓÚÑáéíóúñ])")
        if any(patron.search(l) and re.search(r"[a-záéíóúñ]", l) for l in (fuentes or "").splitlines()):
            siglas.add(palabra)
    return siglas


def restaurar_siglas(perfil: str, titulo: str, siglas: Set[str]) -> str:
    """El título en minúscula del perfil (pauta) conserva sus siglas: «técnico de soporte it» → «técnico de soporte IT»."""
    if not titulo:
        return perfil
    patron = re.compile(r"\s+".join(re.escape(p) for p in titulo.lower().split()), re.IGNORECASE)

    def normal(m: re.Match) -> str:
        palabras = m.group(0).split()
        if m.group(0).isupper():
            # Copiado en mayúsculas desde el título: va como texto, con mayúscula solo al abrir la oración
            palabras = [p.lower() for p in palabras]
            if m.start() == 0 or perfil[:m.start()].rstrip().endswith((".", ":", ";")):
                palabras[0] = palabras[0][:1].upper() + palabras[0][1:]
        return " ".join(p.upper() if p.upper() in (siglas or set()) else p for p in palabras)

    return patron.sub(normal, perfil)


def aplicar(b: Borrador, cambios: Any) -> Set[str]:
    """Aplica `{"perfil": ..., "lineas": [{"id", "texto"}]}`. Solo las líneas que existen; devuelve sus ids."""
    aplicados: Set[str] = set()
    if not isinstance(cambios, dict):
        return aplicados
    perfil = re.sub(r"\s+", " ", str(cambios.get("perfil") or "")).strip()
    if perfil:
        b.perfil.texto = perfil
        aplicados.add("P1")
    for c in cambios.get("lineas") or []:
        if not isinstance(c, dict):
            continue
        linea = b.por_id(str(c.get("id") or "").strip())
        # Solo la marca de viñeta: «**Etiqueta:**» de Habilidades empieza con asteriscos
        texto = re.sub(r"^(?:[•\-]|\*(?!\*))\s*", "", re.sub(r"\s+", " ", str(c.get("texto") or "")).strip()).strip()
        if linea and texto:
            linea.texto = texto
            aplicados.add(linea.id)
    return aplicados


def _lineas_para_ia(b: Borrador) -> str:
    salida = [f"P1 (perfil, a escribir): {b.perfil.texto or '(vacío)'}"]
    for bloque in b.experiencia:
        tope = f" — sintetizá cada viñeta en hasta {bloque.largo_max} caracteres" if bloque.largo_max else ""
        salida.append(f"[{bloque.encabezado.lstrip('# ')}{tope}]")
        salida += [f"{l.id}: {l.texto}" for l in bloque.vinetas]
    salida.append("[HABILIDADES]")
    salida += [f"{l.id}: {l.texto}" for l in b.habilidades]
    return "\n".join(salida)


_FORMATO_RESPUESTA = """Respondé SOLO con este JSON:
{"perfil": "<el perfil>", "lineas": [{"id": "V3", "texto": "<la viñeta ajustada>"}], "keywords_cv": {"<keyword>": "<el término exacto que usaste en el CV>"}}
En "lineas" van solo las líneas que cambiaste, con su id. Sin markdown alrededor."""


def cobertura(b: Borrador, keywords: List[str]) -> Tuple[List[str], List[str]]:
    """(faltan, cubiertas) según la misma regla de la auditoría."""
    from core.auditor import evaluate_ats_compliance
    ats = evaluate_ats_compliance(b.markdown(), keywords, [bl.empresa for bl in b.experiencia])
    return list(ats.get("missing_keywords") or []), list(ats.get("found_keywords") or [])


_NOTA_INGLES = ("\nEl CV es en inglés y las keywords están en castellano: usá el término en inglés de la oferta y "
                "devolvé en keywords_cv, para CADA keyword, el término exacto que quedó en el CV.")


def pedido_ajuste(b: Borrador, oferta: str, obligatorias: List[str], habilitados: List[str]) -> str:
    idioma = "inglés" if b.idioma == EN else "castellano"
    faltan, cubiertas = (obligatorias, []) if b.idioma == EN else cobertura(b, obligatorias)
    # Fuertes del perfil: las keywords que la experiencia elegida ya muestra (evidencia real)
    fuertes = cubiertas if len(cubiertas) >= pautas_cv.PERFIL_MIN_FUERTES else obligatorias
    extra = [h for h in habilitados if h not in obligatorias]
    if b.seleccion_ia:
        # Los términos de la oferta ya los puso el código, cada uno en la línea que lo demuestra
        terminos = ("- Los términos de la oferta ya están puestos en las líneas que los demuestran y en HABILIDADES "
                    "TÉCNICAS: no los saques ni los muevas. Si uno encaja con naturalidad en el contexto de una viñeta, "
                    "podés nombrarlo también ahí con el término de la oferta, sin cambiar lo que pasó ni agregar cifras.")
        if extra:
            terminos += ("\n- El CV demuestra también esto, sin nombrarlo: podés nombrarlo en el perfil con el término "
                         "de la oferta: " + "; ".join(extra) + ".")
    else:
        terminos = f"""- Incorporá lo que el matching habilita aunque no esté en las líneas, con los términos de la oferta, en la viñeta o en
  la línea de habilidades donde esa práctica encaja: {'; '.join(habilitados) or '(nada)'}.
{('- Habilitado por transferible (nombralo con el término de la oferta, sin decir que lo usaste en un trabajo donde no figura): ' + '; '.join(extra)) if extra else ''}"""
    if b.simple:
        # CV simple: el puesto no pide conocimientos técnicos
        terminos += ("\n- CV SIMPLE: el puesto no pide conocimientos técnicos. Contá cada viñeta y las habilidades en "
                     "lenguaje simple, sin nombres de herramientas, siglas ni jerga técnica salvo que la oferta los "
                     "pida; quedate con lo que sirve al puesto (atención, organización, coordinación, comunicación). "
                     "El perfil, sobre lo que pide el puesto.")
    return f"""Ajustá este CV de 1 página a la oferta. Las líneas son el texto real del candidato, elegido de su CV maestro.
Hacé solo esto:
- Escribí el perfil (P1) para esta oferta, en {idioma}.
- En las viñetas (V) y en las habilidades (S), mejorá la redacción sin cambiar lo que dice cada línea ni agregar hechos.
{terminos}
- Todo el CV en {idioma}.{_NOTA_INGLES if b.idioma == EN else ''}

{pautas_cv.pautas(faltan, b.idioma, cubiertas, titulo=b.titulo, areas=b.areas, fuertes=fuertes,
                  con_evidencia=b.seleccion_ia)}

{REGLA_DATOS_DE_TERCEROS.format(tag='oferta')}
{delimitar_datos_de_terceros(oferta, 'oferta')}

LÍNEAS DEL CV:
{_lineas_para_ia(b)}

{_FORMATO_RESPUESTA}"""


def pedido_correccion(b: Borrador, hallazgos: List[Hallazgo], keywords: List[str]) -> str:
    faltan, cubiertas = cobertura(b, keywords)
    detalle = "\n".join(f"- {h.linea or 'general'}: {h.motivo}" for h in hallazgos)
    return f"""Una auditoría independiente rechazó este CV. Corregí SOLO lo que falló, en las líneas indicadas (si dice
«general», elegí la línea donde corresponde). No toques el resto. PREVALECE LA AUDITORÍA: si lo que pide choca con
una pauta de abajo, hacé lo que pide la auditoría; si propone una reescritura, usala (sin inventar datos).

QUÉ FALLÓ:
{detalle}

{pautas_cv.pautas(faltan, b.idioma, cubiertas, titulo=b.titulo, areas=b.areas, fuertes=keywords,
                  con_evidencia=b.seleccion_ia)}

LÍNEAS DEL CV:
{_lineas_para_ia(b)}

{_FORMATO_RESPUESTA}"""


# ---------------------------------------------------------------- auditoría independiente

_RE_CIFRA = re.compile(r"\d+(?:[.,]\d+)?")


# Una cifra de la oferta es una cantidad (no un nombre como «Office 365» o «Tier 2») si la sigue una unidad
_RE_CANTIDAD = re.compile(r"\s*\+?\s*(?:%|a[ñn]os?|years?|yrs?|meses|months?|semanas|weeks?|d[ií]as|days?|horas|hours?"
                          r"|personas|people|usuarios|users|clientes|clients)\b", re.IGNORECASE)


# Nivel de idioma (MCER): solo el que dice el perfil del candidato, nunca el que pide la oferta
_RE_NIVEL_IDIOMA = re.compile(r"(?<![A-Za-z0-9])[ABC][12]\+?(?![A-Za-z0-9])")


def cifras_de_terminos(texto: str) -> Set[str]:
    """Cifras que son parte de un nombre en los términos habilitados por el matching (nunca cantidades)."""
    return {m.group(0) for m in _RE_CIFRA.finditer(texto or "") if not _RE_CANTIDAD.match(texto, m.end())}


def inventos(b: Borrador, maestro: str, personales: str = "", terminos: str = "") -> List[Hallazgo]:
    """Cifras, años o fechas que no están en la línea original ni en el maestro (ni en los datos personales ni en
    los nombres de los términos habilitados)."""
    permitidas = set(_RE_CIFRA.findall(f"{maestro}\n{personales}")) | cifras_de_terminos(terminos)
    niveles = {n.rstrip("+") for n in _RE_NIVEL_IDIOMA.findall(f"{maestro}\n{personales}")}
    hallazgos = []
    for l in b.editables():
        nuevos = [n for n in _RE_NIVEL_IDIOMA.findall(l.texto) if n.rstrip("+") not in niveles]
        if nuevos:
            hallazgos.append(Hallazgo(l.id, f"nivel de idioma que no está en el perfil: {', '.join(dict.fromkeys(nuevos))}"))
        nuevas = [c for c in _RE_CIFRA.findall(l.texto) if c not in set(_RE_CIFRA.findall(l.original))
                  and c not in permitidas]
        if nuevas:
            hallazgos.append(Hallazgo(l.id, f"cifra que no está en el maestro: {', '.join(dict.fromkeys(nuevas))}"))
    return hallazgos


def largos(b: Borrador) -> List[Hallazgo]:
    hallazgos = []
    if len(b.perfil.texto) > pautas_cv.LARGO_MAX_PERFIL:
        hallazgos.append(Hallazgo("P1", f"perfil de {len(b.perfil.texto)} caracteres (máximo {pautas_cv.LARGO_MAX_PERFIL})"))
    for bloque in b.experiencia:
        for l in bloque.vinetas:
            # El texto del maestro vale tal cual (hasta el tope del auditor): el límite es para lo que agrega la IA.
            # Con «sintetizar» (largo_max_vineta del perfil), ese tope manda
            tope = bloque.largo_max or max(pautas_cv.LARGO_MAX_VINETA, min(len(l.original), pautas_cv.LARGO_MAX_AUDITOR))
            if len(l.texto) > tope:
                hallazgos.append(Hallazgo(l.id, f"viñeta de {len(l.texto)} caracteres (máximo {tope})"))
    for l in b.habilidades:
        tope = max(pautas_cv.LARGO_MAX_HABILIDAD, len(l.original) + 20)  # el maestro puede traer líneas largas
        if len(l.texto) > tope:
            hallazgos.append(Hallazgo(l.id, f"habilidades de {len(l.texto)} caracteres (máximo {tope})"))
    return hallazgos


def control_estructura(b: Borrador, maestro: str, estructura: Optional["estructura_cv.Estructura"] = None,
                        ingles: bool = False) -> List[Hallazgo]:
    """La experiencia respeta los cargos del maestro: cada encabezado es un cargo real con su título y
    sus fechas (o, con «segun_oferta», el título unificado del perfil con el período de la empresa); con «todos»
    están todos los cargos de la empresa; cada cargo tiene al menos 1 viñeta y solo viñetas propias."""
    estructura = estructura_cv.por_defecto(estructura)
    hallazgos: List[Hallazgo] = []
    presentes = [bl.encabezado.strip() for bl in b.experiencia]
    for g in roles_por_empresa(parse_roles(maestro)):
        regla = estructura.regla(g["empresa"])
        cargos = {encabezado_canonico(r["rol"], g["empresa"], r.get("desde", ""), r.get("hasta", "")): r
                  for r in g["roles"]}
        unificado = encabezado_canonico(regla.titulo(ingles), g["empresa"], g["desde"], g["hasta"])
        unico = b.cargos_simples.get(g["empresa"])
        if unico:
            if [e for e in presentes if e == unico or e in cargos] != [unico]:
                hallazgos.append(Hallazgo(None, f"{g['empresa']}: en el CV general va un solo cargo ({unico})"))
            continue
        de_empresa = [e for e in presentes if e in cargos or e == unificado]
        if not de_empresa:
            hallazgos.append(Hallazgo(None, f"falta la empresa {g['empresa']} en la experiencia"))
        elif unificado in de_empresa and not (regla.cargos == estructura_cv.SEGUN_OFERTA and len(de_empresa) == 1):
            hallazgos.append(Hallazgo(None, f"{g['empresa']}: el título unificado solo vale solo y con «segun_oferta»"))
        elif unificado not in de_empresa and set(de_empresa) != set(cargos):
            faltan = [r["rol"] for e, r in cargos.items() if e not in de_empresa]
            hallazgos.append(Hallazgo(None, f"{g['empresa']}: faltan los cargos {', '.join(faltan)}"))
    reales = set()
    for g in roles_por_empresa(parse_roles(maestro)):
        regla = estructura.regla(g["empresa"])
        reales |= {encabezado_canonico(r["rol"], g["empresa"], r.get("desde", ""), r.get("hasta", "")) for r in g["roles"]}
        if regla.cargos == estructura_cv.SEGUN_OFERTA:
            reales.add(encabezado_canonico(regla.titulo(ingles), g["empresa"], g["desde"], g["hasta"]))
    reales |= set(b.cargos_simples.values())
    for bl in b.experiencia:
        if bl.encabezado.strip() not in reales:
            hallazgos.append(Hallazgo(None, f"encabezado que no es un cargo real del maestro: {bl.encabezado.strip()}"))
        if not bl.vinetas:
            hallazgos.append(Hallazgo(None, f"{bl.encabezado.strip()}: el cargo no tiene viñetas (mínimo 1)"))
        if not bl.relevante and len(bl.vinetas) > VINETAS_NO_RELEVANTE:
            hallazgos.append(Hallazgo(bl.vinetas[-1].id, f"{bl.encabezado.strip()}: el cargo no suma para la oferta y "
                                                         f"lleva {len(bl.vinetas)} viñetas (máximo {VINETAS_NO_RELEVANTE})"))
        ajenas = [l.id for l in bl.vinetas if bl.originales and l.original not in bl.originales]
        if ajenas:
            hallazgos.append(Hallazgo(ajenas[0], f"{bl.encabezado.strip()}: viñetas de otro cargo ({', '.join(ajenas)})"))
    return hallazgos


# El nivel de un idioma junto a la palabra que lo acompaña: «inglés (C1)», «inglés C1», «C1 English»
_RE_NIVEL_CON_PALABRA = (re.compile(r"(\S+)\s*\(\s*[ABC][12]\+?\s*\)"),
                         re.compile(r"(\S+)\s+[ABC][12]\+?(?![A-Za-z0-9])"),
                         re.compile(r"(?<![A-Za-z0-9])[ABC][12]\+?\s+(\S+)"))


# Un CV en inglés no lleva castellano (oferta 6950: «Experience in … documentación técnica, equipos
# internacionales»): una palabra con tilde o eñe que no está en el maestro (nombres propios) o un conector castellano
_RE_TILDE = re.compile(r"\b\w*[áéíóúñÁÉÍÓÚÑ]\w*\b")
_RE_CONECTOR_ES = re.compile(r"\b(?:y|con|para|del|las|los|una|desde|sobre)\b")


def control_mezcla_idioma(b: Borrador, maestro: str) -> List[Hallazgo]:
    if b.idioma != EN:
        return []
    propias = {p.lower() for p in _RE_TILDE.findall(maestro or "")}
    hallazgos = []
    for l in b.editables():
        ajenas = [p for p in _RE_TILDE.findall(l.texto) if p.lower() not in propias]
        ajenas += _RE_CONECTOR_ES.findall(re.sub(r"\*\*[^*]*\*\*", " ", l.texto))
        if ajenas:
            hallazgos.append(Hallazgo(l.id, f"el CV va en inglés: traducí {', '.join('«' + p + '»' for p in dict.fromkeys(ajenas))}"))
    return hallazgos


def control_idiomas(b: Borrador) -> List[Hallazgo]:
    """El nivel de un idioma va una sola vez, en IDIOMAS y como lo dice el perfil («Avanzado»): un
    nivel MCER en el perfil, las viñetas o las habilidades se marca, con la reescritura sin el nivel."""
    if not b.idiomas:
        return []
    hallazgos = []
    for l in b.editables():
        if not _RE_NIVEL_IDIOMA.search(l.texto):
            continue
        m = next((x for x in (r.search(l.texto) for r in _RE_NIVEL_CON_PALABRA) if x), None)
        senal = {"frase": m.group(0), "patron": "nivel de idioma repetido", "reescritura": m.group(1)} if m else None
        hallazgos.append(Hallazgo(l.id, "el nivel de idioma va solo en IDIOMAS: sacalo de esta línea", senal=senal))
    return hallazgos


# ---------------------------------------------------------------- cobertura de requisitos
# Todo lo que es MATCH o TRANSFERIBLE aparece en el CV con el término de la oferta: en HABILIDADES TÉCNICAS o
# integrado en la viñeta donde encaja, aunque el maestro no lo nombre. Antes, lo que ninguna viñeta del maestro
# demostraba se descartaba en silencio y el puntaje lo seguía contando (oferta 11257: 99 % sin Grafana ni Dynatrace).

def _texto_cubierto(b: Borrador) -> str:
    """Viñetas y habilidades: nombrar algo solo en el perfil no lo muestra."""
    return "\n".join([l.texto for bl in b.experiencia for l in bl.vinetas] + [l.texto for l in b.habilidades])


def _requisitos(analisis: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in analisis.get("requisitos") or [] if isinstance(r, dict)]


def control_cobertura(b: Borrador, analisis: Dict[str, Any],
                      equivalentes: Optional[Dict[str, str]] = None) -> List[Hallazgo]:
    from core.cobertura_requisitos import faltantes
    return [Hallazgo(None, f"falta «{f.termino}» ({f.estado.lower()}, requisito «{f.requisito}»): va en "
                           f"HABILIDADES TÉCNICAS o integrado en la viñeta donde encaja")
            for f in faltantes(_texto_cubierto(b), _requisitos(analisis), idioma=b.idioma, equivalentes=equivalentes)]


def _contexto_requisito(analisis: Dict[str, Any], nombre: str) -> Set[str]:
    r = next((r for r in _requisitos(analisis) if r.get("nombre") == nombre), {})
    return _tokens(" ".join(str(r.get(k) or "") for k in ("nombre", "texto", "equivalencia", "justificacion")))


def _exigido_en(texto: str, analisis: Dict[str, Any], idioma: str) -> bool:
    """El texto (un ítem de habilidades) muestra algún término exigido."""
    from core.cobertura_requisitos import exigidos, presente
    return any(presente(a, texto) for _, alternativas in exigidos(_requisitos(analisis), idioma) for a in alternativas)


def _poner(linea: Linea, termino: str, analisis: Dict[str, Any], idioma: str, recortar: bool) -> bool:
    """Suma el término a la línea sin pasar LARGO_MAX_HABILIDAD; con `recortar`, saca antes ítems afines (no
    exigidos), del último al primero. Si no entra, no cambia nada."""
    etiqueta, items = items_de_habilidad(linea.texto)
    items = items + [termino]
    while len(_linea_de_habilidad(etiqueta, items)) > pautas_cv.LARGO_MAX_HABILIDAD:
        afines = [it for it in items[:-1] if not _exigido_en(it, analisis, idioma)] if recortar else []
        if not afines:
            return False
        items.remove(afines[-1])
    linea.texto = _linea_de_habilidad(etiqueta, items)
    return True


def asegurar_cobertura(b: Borrador, analisis: Dict[str, Any], equivalentes: Optional[Dict[str, str]] = None,
                       maestro: str = "") -> List[str]:
    """Agrega a HABILIDADES TÉCNICAS, con el término de la oferta, lo exigido que el CV no muestra, sin pasar el largo
    de una línea: 1) en el grupo que más se parece (por el requisito, su equivalencia y su justificación) si entra;
    2) si hay lugar, en una línea nueva con la etiqueta del grupo del maestro que más se parece; 3) en otra línea donde
    entre; 4) sacando ítems afines (no exigidos). Devuelve los términos agregados."""
    from core.cobertura_requisitos import faltantes, presente
    agregados: List[str] = []
    grupos_maestro = [items_de_habilidad(h) for h in _habilidades_del_maestro(maestro)] if maestro else []
    for f in faltantes(_texto_cubierto(b), _requisitos(analisis), idioma=b.idioma, equivalentes=equivalentes):
        if presente(f.termino, _texto_cubierto(b)):
            continue  # otro requisito ya lo agregó
        contexto = _contexto_requisito(analisis, f.requisito) | _tokens(f.termino)
        lineas = sorted(b.habilidades, key=lambda l: -len(contexto & _tokens(l.texto)))
        actuales = {items_de_habilidad(l.texto)[0].casefold() for l in b.habilidades}
        nuevas = sorted(((len(contexto & _tokens(f"{et} {' '.join(its)}")), et) for et, its in grupos_maestro
                         if et and et.casefold() not in actuales), key=lambda o: -o[0])
        parecido = len(contexto & _tokens(lineas[0].texto)) if lineas else 0
        puesto = bool(lineas) and _poner(lineas[0], f.termino, analisis, b.idioma, recortar=False)
        if not puesto and len(b.habilidades) < MAX_LINEAS_HABILIDADES and (not lineas or not parecido or nuevas):
            etiqueta = nuevas[0][1] if nuevas and nuevas[0][0] >= parecido else ""
            b.habilidades.append(Linea(f"S{len(b.habilidades) + 1}", _linea_de_habilidad(etiqueta, [f.termino]), ""))
            puesto = True
        for recortar in (False, True):
            for linea in lineas:
                puesto = puesto or _poner(linea, f.termino, analisis, b.idioma, recortar)
        if not puesto and b.habilidades:  # no entra en ninguna: mejor largo que ausente (lo marca el control de largos)
            etiqueta, items = items_de_habilidad(lineas[0].texto)
            lineas[0].texto = _linea_de_habilidad(etiqueta, items + [f.termino])
        elif not puesto:
            b.habilidades.append(Linea("S1", _linea_de_habilidad("", [f.termino]), ""))
        agregados.append(f.termino)
    # Una línea que la IA alargó: salen ítems afines hasta el máximo
    for linea in b.habilidades:
        etiqueta, items = items_de_habilidad(linea.texto)
        while len(_linea_de_habilidad(etiqueta, items)) > pautas_cv.LARGO_MAX_HABILIDAD:
            afines = [it for it in items if not _exigido_en(it, analisis, b.idioma)]
            if not afines or len(items) == 1:
                break
            items.remove(afines[-1])
        if items != items_de_habilidad(linea.texto)[1]:
            linea.texto = _linea_de_habilidad(etiqueta, items)
    return agregados


def control_habilidades(b: Borrador, maestro: str, terminos: str = "") -> List[Hallazgo]:
    """Como mucho MAX_LINEAS_HABILIDADES líneas; cada ítem sale del maestro o de un término habilitado por el
    matching (nunca una herramienta que no aparece en ninguno de los dos)."""
    hallazgos: List[Hallazgo] = []
    if len(b.habilidades) > MAX_LINEAS_HABILIDADES:
        hallazgos.append(Hallazgo(None, f"{len(b.habilidades)} líneas de habilidades (máximo {MAX_LINEAS_HABILIDADES})"))
    conocidos = _tokens(f"{maestro}\n{terminos}")
    for l in b.habilidades:
        ajenos = [it for it in items_de_habilidad(l.texto)[1] if _tokens(it) and not (_tokens(it) & conocidos)]
        if ajenos:
            hallazgos.append(Hallazgo(l.id, f"habilidades que no están en el maestro ni en el matching: {', '.join(ajenos)}"))
    return hallazgos


_RE_PRETERITO_ES = re.compile(r"^[A-ZÁÉÍÓÚ][a-záéíóúñ]+[éí]\b")
_RE_PASADO_EN = re.compile(r"^[A-Z][a-z]+ed\b|^(?:Led|Built|Ran|Wrote|Drove|Made|Took|Set|Kept|Brought|Oversaw|Grew|Cut)\b")
# El perfil va en 3ª persona impersonal: pronombres de 1ª persona y los verbos de 1ª persona del
# presente con los que suele abrir una oración. Los que también son sustantivos («Desarrollo de…», «Trabajo en
# equipo», «Monitoreo de…») cuentan solo si no los sigue «de», «del» o «en equipo»
_VERBOS_1A_ES = ("gestiono|atiendo|analizo|resuelvo|coordino|administro|brindo|realizo|hago|aplico|defino|implemento|"
                 "lidero|soy|tengo|cuento|poseo|integro|construyo|automatizo|articulo|organizo|acompaño|mantengo|"
                 "ejecuto|elaboro|preparo|controlo|redacto|vendo|asesoro|reparo|instalo|configuro|relevo|traduzco|"
                 "convierto|pruebo|valido|superviso|planifico|combino|aporto|busco|domino|conozco|manejo|uso|utilizo|"
                 "trabajo|desarrollo|diseño|monitoreo|registro|documento|soporto|ofrezco|sumo|me desempeño|me especializo")
_RE_1A_PERSONA_ES = re.compile(rf"\b(?:me|mi|mis|yo|conmigo)\b|(?:^|[.;:]\s+)(?:{_VERBOS_1A_ES})\b(?!\s+(?:de|del|en equipo)\b)",
                               re.IGNORECASE)
_RE_1A_PERSONA_EN = re.compile(r"\b(?:I|I'm|I've|I'd|my|me|myself)\b")


def control_perfil(b: Borrador, keywords: List[str]) -> List[Hallazgo]:
    """El perfil es un resumen alineado al título, con los años del área y los fuertes que matchean."""
    from core.auditor import evaluate_ats_compliance
    from core.cv_guardas import _RE_ANIOS, corregir_anios
    texto = b.perfil.texto.strip()
    hallazgos = []
    if (_RE_PASADO_EN if b.idioma == EN else _RE_PRETERITO_ES).match(texto):
        hallazgos.append(Hallazgo("P1", "el perfil es una viñeta más (empieza con un verbo en pasado): tiene que ser un resumen"))
    if (_RE_1A_PERSONA_EN if b.idioma == EN else _RE_1A_PERSONA_ES).search(texto):
        hallazgos.append(Hallazgo("P1", "el perfil va en 3ª persona impersonal («Profesional con experiencia en…», "
                                        "«Experiencia en…»), sin «yo», «me», «mi» ni verbos en 1ª persona"))
    if b.titulo and not (_tokens(b.titulo) & _tokens(texto)):
        hallazgos.append(Hallazgo("P1", f"el perfil no se alinea con el título «{b.titulo}» (no nombra ese rol ni su área)"))
    if b.simple and _RE_ANIOS.search(texto):
        hallazgos.append(Hallazgo("P1", "CV simple: el perfil no menciona años de experiencia"))
    if b.areas:
        if not _RE_ANIOS.search(texto):
            hallazgos.append(Hallazgo("P1", "el perfil no dice los años del área (de anios_por_area)"))
        _, cambios = corregir_anios(texto, b.areas)
        if cambios:
            hallazgos.append(Hallazgo("P1", f"años del perfil que no son los del área: {'; '.join(cambios)}"))
    if len(keywords) >= pautas_cv.PERFIL_MIN_FUERTES:
        fuertes = evaluate_ats_compliance(texto, keywords, [])["found_keywords"]
        if len(fuertes) < pautas_cv.PERFIL_MIN_FUERTES:
            hallazgos.append(Hallazgo("P1", f"el perfil nombra {len(fuertes)} fuertes que matchean (mínimo "
                                            f"{pautas_cv.PERFIL_MIN_FUERTES}, de: {'; '.join(keywords)})"))
    return hallazgos


def _linea_de(b: Borrador, fragmento: str) -> Optional[str]:
    frag = _norm(re.sub(r"\s*\(.*$", "", fragmento)).strip(" «»\"")
    if not frag:
        return None
    for l in b.editables():
        if frag in _norm(l.texto):
            return l.id
    return None


def ubicar(b: Borrador, reporte: Dict[str, Any]) -> List[Hallazgo]:
    """Los hallazgos del auditor, cada uno en su línea cuando se puede ubicar."""
    if reporte.get("approved"):
        return []
    from core.auditor import _es_impacto_vago, _RE_APERTURA_PERFIL  # reglas del auditor, no una copia
    hallazgos: List[Hallazgo] = []
    anti = reporte.get("anti_ai") or {}
    citas = {s.get("frase") for s in anti.get("senales_llm") or [] if isinstance(s, dict)}
    for s in anti.get("senales_llm") or []:
        if isinstance(s, dict) and s.get("frase"):
            # La reescritura que propone la auditoría va a la corrección: prevalece la auditoría
            sugerida = f"; la auditoría propone: «{s['reescritura']}»" if s.get("reescritura") else ""
            hallazgos.append(Hallazgo(_linea_de(b, s["frase"]),
                                      f"«{s['frase']}» ({s.get('patron') or 'señal de IA'}){sugerida}", senal=s))
    for palabra in anti.get("detected_words") or []:
        texto = str(palabra)
        if any(c and c in texto for c in citas):
            continue
        if texto.startswith("impacto vago"):
            vagas = [l.id for l in b.editables() if _es_impacto_vago(l.texto)]
            hallazgos += [Hallazgo(v, texto) for v in vagas] or [Hallazgo(None, texto)]
        elif texto.startswith("apertura formulaica"):
            hallazgos.append(Hallazgo("P1", texto))
        elif texto.startswith("raya"):
            rayas = [l.id for l in b.editables() if re.search(r"[—–]", l.texto)]
            hallazgos += [Hallazgo(v, texto) for v in rayas] or [Hallazgo(None, texto)]
        else:
            hallazgos.append(Hallazgo(_linea_de(b, texto), f"«{texto}» (frase de IA)" if "(" not in texto else texto))
    ats = reporte.get("ats") or {}
    if float(ats.get("keyword_coverage", 1.0)) < 0.80:  # el mismo piso del auditor
        for kw in ats.get("missing_keywords") or []:
            hallazgos.append(Hallazgo(None, f"falta la keyword «{kw}» (alcanza con: {', '.join(pautas_cv.nucleo(kw))})"))
    if float(ats.get("ats_score", 100.0)) < 80.0:
        hallazgos.append(Hallazgo(None, "puntaje ATS bajo: " + "; ".join(map(str, ats.get("deducciones") or []))))
    for emp in ats.get("missing_experiences") or []:
        hallazgos.append(Hallazgo(None, f"falta la empresa «{emp}»"))
    if not hallazgos:
        for obs in (reporte.get("hallazgos_unificados") or {}).get("lista_observaciones") or []:
            hallazgos.append(Hallazgo(None, str(obs)))
    return hallazgos


# ---------------------------------------------------------------- ticket

def _ticket(**kw) -> int:
    from core import tickets
    return tickets.reportar_error(**kw)


# ---------------------------------------------------------------- flujo

@dataclass
class Resultado:
    markdown: str
    aprobado: bool
    correcciones: int
    reporte: Dict[str, Any]
    keywords: List[str]
    keywords_cv: Dict[str, str]
    pdf: Optional[Path]
    segundos: Dict[str, float]
    borrador: Borrador


class FlujoCV:
    """Un CV de 1 página desde el maestro para una oferta. `ia(prompt) -> dict` es la llamada de ajuste."""

    def __init__(self, *, maestro: str, personales: str, analisis: Dict[str, Any], oferta: str, titulo: str,
                 idioma: str = ES, ia: Optional[Callable[[str], Awaitable[Any]]] = None, auditor=None,
                 post_id: Optional[int] = None, nombre_pdf: Optional[str] = None, maestro_fuente: str = "",
                 anios_por_area: Optional[List[Dict[str, Any]]] = None,
                 al_ajustar: Optional[Callable[[str], None]] = None,
                 al_auditar: Optional[Callable[[], Awaitable[None]]] = None,
                 estructura: Optional["estructura_cv.Estructura"] = None):
        self.maestro, self.personales, self.analisis = maestro, personales, analisis
        # Cómo se muestran los cargos de cada empresa (perfil, `core/estructura_cv.py`)
        self.estructura = estructura_cv.por_defecto(estructura)
        self.maestro_fuente = maestro_fuente  # maestro en castellano cuando el CV es en inglés (cifras permitidas)
        self.anios_por_area = list(anios_por_area or [])
        # Se llama con el CV ajustado, antes de auditarlo: el mail se escribe mientras se audita el CV
        self.al_ajustar = al_ajustar
        # Se llama una vez, al empezar la primera auditoría: el HUD pasa a la fase del Auditor
        self.al_auditar = al_auditar
        self.oferta, self.titulo, self.idioma = oferta, titulo, idioma
        self.ia = ia or self._ia_agy
        self._auditor = auditor
        self.post_id = post_id
        self.nombre_pdf = nombre_pdf
        self.pdf: Optional[Path] = None
        self.segundos: Dict[str, float] = {}
        self.historial: List[List[Hallazgo]] = []  # hallazgos de cada auditoría, en orden
        self.seleccion = ""  # «ia» o «codigo» (la IA falló y se eligió por palabras)
        self.reemplazos: List[Dict[str, str]] = []  # los términos de la oferta que puso el código, y dónde
        self.tipo_cv, self.motivo_tipo = "", ""  # lo que decidió la IA de la selección (simple o completo) y por qué
        self.renglones_sobrantes = 0  # de la última compilación: renglones que pasaron a la 2.ª página
        # Líneas que la revisión de estilo con IA ya aprobó, con su texto: si no cambian, no se vuelven a juzgar
        self.aprobadas: Dict[str, str] = {}
        # Requisito → término que la IA dijo haber usado en el CV (el CV en inglés): vale para la cobertura
        self.keywords_cv: Dict[str, str] = {}

    @property
    def auditor(self):
        if self._auditor is None:
            from core.auditor import AuditorAgent
            self._auditor = AuditorAgent(effort="low")
        return self._auditor

    async def _ia_agy(self, prompt: str, *, effort: str = EFFORT_AJUSTE, timeout_s: int = TIMEOUT_AJUSTE_S) -> Any:
        from core.bridge import AntigravityBridge, radar_agy_cwd
        with radar_agy_cwd() as cwd:
            return await AntigravityBridge(effort=effort).run_prompt(
                prompt, json_mode=True, timeout_seconds=timeout_s, effort=effort, cwd=cwd)

    def _keywords(self, obligatorias: List[str], keywords_cv: Dict[str, str]) -> List[str]:
        if self.idioma != EN:
            return obligatorias
        return list(dict.fromkeys(str(keywords_cv.get(k) or k) for k in obligatorias))

    async def _medir(self, etapa: str, coro):
        if etapa == "auditoria" and "auditoria" not in self.segundos:
            from core import progreso
            await progreso.reportar("auditar")  # el HUD avanza del ajuste a la auditoría
            if self.al_auditar is not None:
                await self.al_auditar()
        t0 = time.monotonic()
        try:
            return await coro
        finally:
            self.segundos[etapa] = round(self.segundos.get(etapa, 0.0) + time.monotonic() - t0, 1)

    async def _llamar(self, etapa: str, prompt: str) -> Dict[str, Any]:
        respuesta = await self._medir(etapa, self.ia(prompt))
        if isinstance(respuesta, str):
            try:
                respuesta = json.loads(respuesta)
            except ValueError:
                respuesta = None
        if not isinstance(respuesta, dict):
            raise RuntimeError(f"La IA no devolvió el JSON del {etapa}")
        return respuesta

    async def auditar(self, b: Borrador, keywords: List[str], maestro: str) -> Tuple[Dict[str, Any], List[Hallazgo]]:
        md = b.markdown()
        senales = await self.auditor.revisar_senales_ia(md)
        reporte = self.auditor.audit(md, "", keywords, expected_companies=[bl.empresa for bl in b.experiencia],
                                     senales_llm=senales)
        terminos = "\n".join(" ".join(str(r.get(k) or "") for k in ("nombre", "texto", "equivalencia"))
                             for r in self.analisis.get("requisitos") or [] if isinstance(r, dict) and _habilitado(r))
        anios = " ".join(str(a.get("anios") or "") for a in self.anios_por_area)
        hallazgos = (ubicar(b, reporte)
                     + inventos(b, f"{maestro}\n{self.maestro_fuente}", f"{self.personales}\n{anios}", terminos)
                     + largos(b) + control_perfil(b, keywords)
                     + control_estructura(b, maestro, self.estructura, self.idioma == EN)
                     + control_habilidades(b, f"{maestro}\n{self.maestro_fuente}", terminos)
                     + control_cobertura(b, self.analisis, self.keywords_cv) + control_idiomas(b)
                     + control_mezcla_idioma(b, maestro))  # solo el maestro en inglés: el castellano no vale
        return reporte, hallazgos

    async def _auditar(self, b: Borrador, keywords: List[str]) -> Tuple[Dict[str, Any], List[Hallazgo]]:
        """La auditoría, sin las señales de la revisión con IA sobre líneas que esa revisión ya aprobó y no
        cambiaron (la revisión no es determinista: volver a juzgar lo aprobado encontraba otras cosas cada vez)."""
        reporte, hallazgos = await self._medir("auditoria", self.auditar(b, keywords, self.maestro))
        hallazgos = [h for h in hallazgos if not (h.senal and h.linea and b.por_id(h.linea)
                                                  and self.aprobadas.get(h.linea) == b.por_id(h.linea).texto)]
        marcadas = {h.linea for h in hallazgos if h.senal}
        for l in b.editables():
            if l.id not in marcadas:
                self.aprobadas[l.id] = l.texto
        self.historial.append(hallazgos)
        return reporte, hallazgos

    def _reescribir(self, b: Borrador, hallazgos: List[Hallazgo]) -> List[Hallazgo]:
        """Aplica la reescritura que propone la auditoría cuando su cita está en la línea (la línea queda aprobada:
        es el texto de la auditoría); devuelve los hallazgos que tiene que corregir la IA."""
        from core.auditor import aplicar_reescrituras
        pendientes = []
        for h in hallazgos:
            linea = b.por_id(h.linea) if h.senal and h.linea else None
            nuevo = aplicar_reescrituras(linea.texto, [h.senal])[0] if linea and h.senal.get("reescritura") else None
            if linea and nuevo and nuevo != linea.texto:
                linea.texto = nuevo
                self.aprobadas[linea.id] = nuevo
            else:
                pendientes.append(h)
        return pendientes

    def _mecanica_pendiente(self, hallazgos: List[Hallazgo], mecanicas: int) -> bool:
        """Una corrección más si después de la corrección solo quedan controles por código (keywords, largos,
        estructura) y son otros que antes: la corrección avanzó y lo que falta es mecánico (oferta 482: la
        reescritura de la auditoría sacó una keyword)."""
        anteriores = {(h.linea, h.motivo) for h in self.historial[-2]} if len(self.historial) > 1 else set()
        return (mecanicas < CORRECCIONES_MECANICAS and all(not h.senal for h in hallazgos)
                and {(h.linea, h.motivo) for h in hallazgos} != anteriores)

    async def paginas(self, md: str) -> int:
        """Compila con la plantilla real y cuenta las páginas del PDF."""
        import asyncio
        from pypdf import PdfReader
        from core.pdf_generator import compile_markdown_to_pdf
        ruta = await asyncio.to_thread(compile_markdown_to_pdf, md, self.titulo, self.nombre_pdf, None, None)
        self.pdf = Path(ruta)
        lector = PdfReader(str(ruta))
        # Renglones que pasan a la 2.ª página: cuánto hay que sacar de una vez
        self.renglones_sobrantes = sum(len([l for l in (p.extract_text() or "").splitlines() if l.strip()])
                                       for p in lector.pages[1:])
        return len(lector.pages)

    async def _a_una_pagina(self, b: Borrador, keywords: List[str], sumar: bool = False) -> None:
        """Saca viñetas de menos puntaje (nunca por debajo del mínimo de la empresa) hasta 1 página. Con `sumar`
        (solo antes del ajuste, así toda viñeta pasa por la IA y por la auditoría), si sobra lugar suma del maestro
        las que no agregan señales de IA. Cada prueba compila el PDF."""
        from core.auditor import evaluate_ats_compliance, scan_anti_ai_writing
        compilaciones = 0
        compilado = {"md": None}

        async def contar() -> int:
            nonlocal compilaciones
            compilaciones += 1
            # Cada versión que se compila tiene todo lo exigido: si un recorte o una corrección se llevó un término,
            # vuelve a HABILIDADES TÉCNICAS
            asegurar_cobertura(b, self.analisis, self.keywords_cv, self.maestro)
            compilado["md"] = b.markdown()
            return await self._medir("pagina", self.paginas(compilado["md"]))

        def cobertura() -> float:
            return evaluate_ats_compliance(b.markdown(), keywords, [bl.empresa for bl in b.experiencia])["keyword_coverage"]

        def opciones_sobre(piso: Callable[[Bloque], int], por_cargo: bool = False) -> List[Tuple[Linea, Bloque]]:
            # Por el puntaje original (la relevancia del maestro para la oferta), no por las keywords que la
            # reescritura le haya puesto a cada viñeta; con `por_cargo`, primero el cargo menos relevante
            return sorted(((l, bl) for bl in b.experiencia if len(bl.vinetas) > piso(bl) for l in bl.vinetas
                           if not _sostiene_a_otra(l, bl)),
                          key=lambda c: (c[1].relevancia if por_cargo else 0, c[0].puntaje))

        def lleva_exigido(linea: Linea) -> bool:
            antes = len(control_cobertura(b, self.analisis, self.keywords_cv))
            i = b.habilidades.index(linea)
            b.habilidades.pop(i)
            despues = len(control_cobertura(b, self.analisis, self.keywords_cv))
            b.habilidades.insert(i, linea)
            return despues > antes

        def sacar_item_afin() -> float:
            antes = len(control_cobertura(b, self.analisis, self.keywords_cv))
            for linea in sorted(b.habilidades, key=lambda l: -len(l.texto)):
                etiqueta, items = items_de_habilidad(linea.texto)
                for item in (reversed(items) if len(items) > 1 else []):
                    texto = linea.texto
                    linea.texto = _linea_de_habilidad(etiqueta, [i for i in items if i != item])
                    if len(control_cobertura(b, self.analisis, self.keywords_cv)) <= antes:
                        return max(0.3, _renglones(texto) - _renglones(linea.texto))
                    linea.texto = texto
            return 0.0

        def sin(linea: Linea) -> float:  # la línea de habilidades que menos keywords aporta
            i = b.habilidades.index(linea)
            b.habilidades.pop(i)
            valor = cobertura()
            b.habilidades.insert(i, linea)
            return valor

        paginas = await contar()
        while paginas > 1 and compilaciones < MAX_COMPILACIONES:
            # Orden: 1) las viñetas que superan el mínimo de su cargo; 2) las habilidades, hasta
            # MIN_LINEAS_HABILIDADES; 3) el mínimo del cargo menos relevante, hasta 1. Nunca menos de 1 por cargo.
            # Se saca de una vez lo que pasó a la 2.ª página (oferta 7991: 1 por compilación no alcanzaba)
            objetivo, quitados = max(1.0, float(self.renglones_sobrantes or 0)), 0.0
            while quitados < objetivo:
                opciones = opciones_sobre(lambda bl: bl.minimo)
                # Una línea que lleva algo exigido no sale: `asegurar_cobertura` la volvería a poner (1197)
                removibles = [l for l in b.habilidades if not lleva_exigido(l)]
                if not opciones and len(b.habilidades) > MIN_LINEAS_HABILIDADES and removibles:
                    linea = max(reversed(removibles), key=sin)
                    b.habilidades.remove(linea)
                    quitados += _renglones(linea.texto)
                    continue
                opciones = opciones or opciones_sobre(lambda bl: 1, por_cargo=True)
                if not opciones:
                    # Último recurso: un ítem afín (no exigido) de la línea de habilidades más larga
                    sacado = sacar_item_afin()
                    if not sacado:
                        break
                    quitados += sacado
                    continue
                linea, bloque = opciones[0]
                bloque.vinetas.remove(linea)
                bloque.reserva.insert(0, linea)
                quitados += _renglones(linea.texto)
            if not quitados:
                break
            paginas = await contar()
        if paginas > 1:
            raise CVNoAprobado(f"El CV no entra en 1 página tras {compilaciones} compilaciones")
        senales = set(scan_anti_ai_writing(b.markdown())["detected_words"])
        candidatas = sorted(((l, bl) for bl in b.experiencia for l in bl.reserva if l.puntaje > 0),
                            key=lambda c: -c[0].puntaje) \
            if sumar else []
        for linea, bloque in candidatas:
            if compilaciones >= MAX_COMPILACIONES:
                break
            if scan_anti_ai_writing(f"## {TITULOS[self.idioma]['EXPERIENCIA']}\n• {linea.texto}")["detected_words"]:
                continue
            if linea not in bloque.reserva or _con_su_anterior(linea, bloque) != [linea] or bloque.lleno():
                continue  # remite a una viñeta que no está (sola no se entiende), o el cargo llegó a su tope
            bloque.reserva.remove(linea)
            bloque.vinetas.append(linea)
            if set(scan_anti_ai_writing(b.markdown())["detected_words"]) - senales or await contar() > 1:
                bloque.vinetas.remove(linea)
                bloque.reserva.insert(0, linea)
                if compilaciones >= MAX_COMPILACIONES:
                    break
        if compilado["md"] != b.markdown():
            await contar()  # el PDF que queda es el del texto final

    async def puntuar(self) -> Optional[PuntajesIA]:
        """Etapa de selección: la IA puntúa cada viñeta y cada ítem del maestro para la oferta. Si falla, None (se
        elige por palabras), un ticket y la marca en el reporte."""
        simple = self.estructura.cv_simple
        extra = tuple(_de_seccion(_secciones(self.maestro), *simple.secciones_habilidades)) if simple else ()
        prompt, vinetas, items = pedido_seleccion(self.maestro, self.analisis, self.oferta, self.idioma, extra, simple)
        try:
            kw = {"effort": EFFORT_SELECCION, "timeout_s": TIMEOUT_SELECCION_S} if self.ia == self._ia_agy else {}
            puntajes = puntajes_de(await self._medir("seleccion", self.ia(prompt, **kw)), vinetas, items,
                                   self.analisis)
        except Exception as e:  # noqa: BLE001 — el CV sigue con la selección por palabras
            self.seleccion = "codigo"
            logger.warning("Selección del CV con IA fallida (post %s): %s", self.post_id, e)
            _ticket(componente="cv", tipo="cv_seleccion_ia",
                    mensaje=f"La selección con IA falló y el CV se eligió por palabras: {e}"[:900],
                    datos={"post_id": self.post_id, "segundos": self.segundos})
            return None
        self.seleccion = "ia"
        self.tipo_cv, self.motivo_tipo = puntajes.tipo_cv, puntajes.motivo_tipo
        return puntajes

    async def generar(self) -> Resultado:
        t0 = time.monotonic()
        obligatorias, habilitados = terminos_del_matching(self.analisis)
        puntajes = await self.puntuar()
        if puntajes:
            # Lo que ninguna línea del maestro demuestra no se exige ni se incorpora
            obligatorias = [k for k in obligatorias if k in puntajes.evidenciados]
            habilitados = [h for h in habilitados if h in puntajes.evidenciados]
            self.reemplazos = [{"linea": v, "reemplazar": de, "por": por}
                               for v, cambios in puntajes.reemplazos.items() for de, por in cambios]
            if puntajes.tipo_cv == TIPO_SIMPLE and self.estructura.cv_simple:
                # El rol técnico no va en un CV simple: el titular sale de Mi perfil; vacío, sin titular
                self.titulo = self.estructura.cv_simple.titular_de(self.idioma == EN).upper()
        b = seleccionar(self.maestro, self.personales, self.analisis, idioma=self.idioma, titulo=self.titulo,
                        areas=self.anios_por_area, estructura=self.estructura, puntajes=puntajes)
        if puntajes and self.idioma != EN:
            # Se exige solo lo que el texto elegido ya muestra tal cual; un requisito demostrado sin su término
            # («Adaptabilidad y aprendizaje ágil») queda habilitado para el perfil, sin hacer fallar el CV
            _, cubiertas = cobertura(b, obligatorias)
            habilitados = list(dict.fromkeys(habilitados + [k for k in obligatorias if k not in cubiertas]))
            obligatorias = [k for k in obligatorias if k in cubiertas]
        # La página se llena antes del ajuste, con el lugar del perfil reservado: después solo se puede sacar
        b.perfil.texto = PERFIL_RESERVADO
        llenar = not b.simple or (self.estructura.cv_simple and self.estructura.cv_simple.llenar_pagina)
        await self._a_una_pagina(b, obligatorias, sumar=bool(llenar))
        b.perfil.texto = ""
        respuesta = await self._llamar("ajuste", pedido_ajuste(b, self.oferta, obligatorias, habilitados))
        aplicar(b, respuesta)
        siglas = siglas_del_titulo(self.titulo, f"{self.oferta}\n{self.maestro}")
        b.perfil.texto = restaurar_siglas(b.perfil.texto, self.titulo, siglas)
        keywords_cv = {str(k): str(v) for k, v in (respuesta.get("keywords_cv") or {}).items()} \
            if isinstance(respuesta.get("keywords_cv"), dict) else {}
        keywords = self._keywords(obligatorias, keywords_cv)
        self.keywords_cv = keywords_cv
        await self._a_una_pagina(b, keywords)
        if self.al_ajustar:
            self.al_ajustar(b.markdown())
        reporte, hallazgos = await self._auditar(b, keywords)
        correcciones = mecanicas = 0
        while hallazgos and (correcciones < CORRECCIONES or self._mecanica_pendiente(hallazgos, mecanicas)):
            if correcciones >= CORRECCIONES:
                mecanicas += 1
            correcciones += 1
            # Prevalece la auditoría: su reescritura se aplica tal cual; la IA corrige el resto
            pendientes = self._reescribir(b, hallazgos)
            if pendientes:
                aplicar(b, await self._llamar("correccion", pedido_correccion(b, pendientes, keywords)))
            b.perfil.texto = restaurar_siglas(b.perfil.texto, self.titulo, siglas)
            await self._a_una_pagina(b, keywords)
            reporte, hallazgos = await self._auditar(b, keywords)
        # Prevalece la auditoría también al final: si la corrección dejó una frase que la auditoría sabe cómo
        # reescribir (el perfil del 11090), se aplica su reescritura tal cual y se audita una vez más, sin IA de por medio
        if hallazgos and len(self._reescribir(b, list(hallazgos))) < len(hallazgos):
            b.perfil.texto = restaurar_siglas(b.perfil.texto, self.titulo, siglas)
            await self._a_una_pagina(b, keywords)
            reporte, hallazgos = await self._auditar(b, keywords)
        self.segundos["total"] = round(time.monotonic() - t0, 1)
        if hallazgos:
            motivo = "; ".join(f"{h.linea or 'general'}: {h.motivo}" for h in hallazgos)
            lineas = [{"id": h.linea, "motivo": h.motivo, "texto": (b.por_id(h.linea).texto if h.linea and b.por_id(h.linea) else "")}
                      for h in hallazgos]
            _ticket(componente="cv", tipo="cv_no_aprobado", mensaje=f"CV no aprobado tras {correcciones} corrección: {motivo}"[:900],
                    datos={"post_id": self.post_id, "lineas": lineas, "cv": b.markdown(), "segundos": self.segundos})
            raise CVNoAprobado(f"CV no aprobado tras la corrección: {motivo}")
        if isinstance(reporte, dict):
            reporte["seleccion"] = self.seleccion
            reporte["reemplazos"] = self.reemplazos
            reporte["tipo_cv"] = TIPO_SIMPLE if b.simple else TIPO_COMPLETO
            reporte["motivo_tipo"] = self.motivo_tipo
        return Resultado(b.markdown(), True, correcciones, reporte, keywords, keywords_cv, self.pdf, self.segundos, b)


# ---------------------------------------------------------------- maestro en inglés

PEDIDO_TRADUCCION = """Traducí al inglés este CV maestro, completo y línea por línea. Mantené exactamente la estructura:
los encabezados `## Empresa` (el nombre de la empresa no se traduce), `### Cargo | AAAA - AAAA` (traducí el cargo; las
fechas iguales) y las viñetas `•`. Las secciones van como `## TECHNICAL SKILLS`, `## INTERPERSONAL SKILLS`,
`## EDUCATION` y `## LANGUAGES`. Viñetas con el verbo en pasado al principio, sin pronombres. No agregues, quites ni
cambies ningún hecho, cifra, año, empresa ni herramienta. Respondé SOLO con JSON: {{"maestro_en": "<el texto>"}}

CV MAESTRO:
{maestro}

EDUCACIÓN E IDIOMAS (de los datos personales; van en ## EDUCATION y ## LANGUAGES):
{personales}"""


def _educacion_idiomas(personales: str) -> str:
    perfil = parse_candidate_profile("", personales)
    return "\n".join(["EDUCACIÓN"] + [f"• {e}" for e in perfil["educacion"]] + ["", "IDIOMAS"]
                     + [f"• {i}" for i in perfil["idiomas"]])


async def traducir_maestro(maestro: str, personales: str, ia: Callable[[str], Awaitable[Any]]) -> str:
    """Traducción una sola vez, pendiente de aprobación del usuario (la marca la lleva el archivo)."""
    respuesta = await ia(PEDIDO_TRADUCCION.format(maestro=maestro, personales=_educacion_idiomas(personales)))
    texto = (respuesta or {}).get("maestro_en") if isinstance(respuesta, dict) else None
    if not texto or not parse_roles(texto):
        raise RuntimeError("La traducción del maestro no trajo el formato esperado")
    def cifras(t: str) -> Set[str]:  # 10.000 y 10,000 son la misma cifra
        return {re.sub(r"[.,]", "", c) for c in _RE_CIFRA.findall(t)}

    faltan = cifras(maestro) - cifras(texto)
    if faltan:
        logger.warning("La traducción del maestro perdió cifras: %s", sorted(faltan))
    return f"{MARCA_PENDIENTE}\n{_marca_fuente(maestro, personales)}\n{texto.strip()}\n"


def maestro_en_pendiente(texto: str) -> bool:
    return MARCA_PENDIENTE in (texto or "")


def huella_fuente(maestro: str, personales: str) -> str:
    """Huella de lo que se traduce: el maestro en castellano más la educación y los idiomas."""
    return hashlib.sha256(f"{maestro}\n{_educacion_idiomas(personales)}".encode("utf-8")).hexdigest()[:16]


def _marca_fuente(maestro: str, personales: str) -> str:
    return f"<!-- FUENTE: {huella_fuente(maestro, personales)} -->"


def maestro_en_vigente(texto_en: str, maestro: str, personales: str) -> bool:
    """El maestro en inglés es la traducción del maestro en castellano actual: si el usuario cambió el castellano
    (o la educación o los idiomas), hay que traducir de nuevo, y vuelve a quedar pendiente de aprobación."""
    return _marca_fuente(maestro, personales) in (texto_en or "")


async def asegurar_maestro_en(candidato: Path, ia: Callable[[str], Awaitable[Any]]) -> Tuple[str, bool]:
    """(maestro en inglés, si se tradujo ahora). Traduce solo si falta o quedó viejo."""
    maestro = (candidato / "experiencia.txt").read_text(encoding="utf-8")
    personales = (candidato / "personales.txt").read_text(encoding="utf-8")
    ruta = candidato / MAESTRO_EN
    actual = ruta.read_text(encoding="utf-8") if ruta.exists() else ""
    if actual and maestro_en_vigente(actual, maestro, personales):
        return actual, False
    texto = await traducir_maestro(maestro, personales, ia)
    ruta.write_text(texto, encoding="utf-8")
    return texto, True


def titulo_en_ingles(titulo: str, maestro: str, maestro_en: str) -> Optional[str]:
    """El título decidido (un rol real del maestro) con su traducción del maestro en inglés: los roles están en el
    mismo orden en los dos. None si el título no es un rol del maestro."""
    roles, roles_en = parse_roles(maestro), parse_roles(maestro_en)
    if len(roles) != len(roles_en):
        return None
    buscado = _norm(titulo).strip()
    for r, r_en in zip(roles, roles_en):
        if _norm(r["rol"]).strip() == buscado:
            return r_en["rol"]
    return None
