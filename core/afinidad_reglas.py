"""Reglas determinísticas sin IA para afinidad y detección de brechas duras."""
import copy
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from core import candidato_datos
from core.cv_guardas import _NUMEROS, _RE_ANIOS, es_requisito_logistico
from core.paths import DATA_DIR

logger = logging.getLogger(__name__)

# Roles y stopwords para excluir al extraer la herramienta principal del puesto («núcleo»)
ROLES_Y_STOPWORDS = {
    # Lista de la spec:
    "analyst", "analysts", "analista", "analistas",
    "engineer", "engineers", "ingeniero", "ingenieros", "ingeniera", "ingenieras",
    "manager", "managers",
    "developer", "developers", "desarrollador", "desarrolladores", "desarrolladora", "desarrolladoras",
    "senior", "lead", "specialist", "specialists", "especialista", "especialistas",
    "business",
    "consultor", "consultores", "consultora", "consultoras", "consultant", "consultants",
    "tecnico", "tecnica", "tecnicos", "tecnicas", "technical",
    "funcional", "funcionales", "functional",
    "project", "projects", "proyecto", "proyectos",
    "services", "service", "servicio", "servicios",
    "managed",
    "engineering",
    "operations", "operation", "operaciones", "operacion", "operador", "operadora", "operadores",
    "support", "soporte",
    "convocatoria", "convocatorias",
    "abierta", "abiertas", "abierto", "abiertos",
    # Variantes flexionadas usuales y niveles
    "junior", "ssr", "semisenior", "trainee", "intern", "staff", "head", "director", "directora", "directores",
    "para", "sobre", "with", "from", "into", "about",
}

_RE_CERT = re.compile(
    r"\b(?:certificaci[oó]\w*|certified|certificad[oa]s?|pmp)\b",
    re.IGNORECASE,
)


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower()) if unicodedata.category(c) != "Mn")


def _cifra_de(anios: Any) -> Optional[int]:
    m = re.search(r"\d+", str(anios or ""))
    return int(m.group()) if m else None


# Rango de años («2 a 4 años», «3-5 years», «between 2 and 4 years»): vale el mínimo, que es lo exigido
_RE_RANGO_ANIOS = re.compile(
    r"(?<!\d)(?P<a>\d{1,2})\s*(?:a|-|–|—|to|y|and)\s*(?P<b>\d{1,2})\s*\+?\s*(?:a[nñ]os?|years?)\b",
    re.IGNORECASE,
)


def _extraer_anios(texto: str) -> Optional[int]:
    if not texto:
        return None
    rango = _RE_RANGO_ANIOS.search(texto)
    if rango:
        return min(int(rango.group("a")), int(rango.group("b")))
    m = _RE_ANIOS.search(texto)
    if not m:
        return None
    raw_n = m.group("n")
    if not raw_n:
        return None
    raw_lower = raw_n.lower()
    if raw_lower.isdigit():
        return int(raw_lower)
    return _NUMEROS.get(raw_lower)


def _areas_tematicas(areas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    resultado = []
    for a in areas:
        if not isinstance(a, dict):
            continue
        nom = str(a.get("area", ""))
        norm = _sin_tildes(nom)
        if "total" in norm or "experiencia laboral" in norm:
            continue
        resultado.append(a)
    return resultado


# Palabras de dominio de un área y cómo aparecen en ofertas en inglés (no son datos del candidato:
# el área y sus años salen del perfil). Ej.: «IT Operations, Support…» cuenta para «soporte e incidentes».
_PALABRAS_DE_AREA = {
    "soporte": (r"\bsoporte\b", r"\bsupport\b", r"\bhelp ?desk\b", r"\bservice desk\b"),
    "incidentes": (r"\bincidentes?\b", r"\bincidents?\b"),
    "monitoreo": (r"\bmonitoreo\b", r"\bmonitoring\b"),
    "desarrollo": (r"\bdesarrollo\b", r"\bdevelopment\b", r"\bdeveloper\b"),
}


def _patrones_de_area(nombre_area_norm: str) -> List[str]:
    """Patrones de las palabras de dominio presentes en el nombre del área (en español y su equivalente en inglés)."""
    patrones: List[str] = []
    for palabra, pats in _PALABRAS_DE_AREA.items():
        if re.search(rf"\b{palabra}\b", nombre_area_norm):
            patrones.extend(pats)
    return patrones


def _sinonimos_de_area(nombre_area: str) -> List[str]:
    """Sinónimos del área del candidato (Mi perfil, roles_objetivo.json) cuyo nombre contiene el área."""
    norm = _sin_tildes(nombre_area)
    for area in candidato_datos.roles()["areas"]:
        if any(_sin_tildes(n) in norm for n in area["nombres"]):
            return area["sinonimos"]
    return []


def _buscar_areas_coincidentes(
    texto_req: str,
    thematic_areas: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], int, int]]:
    """Devuelve tuplas (area_dict, anios_int, primera_posicion_en_texto)."""
    norm_req = _sin_tildes(texto_req)
    coincidentes = []

    for a in thematic_areas:
        nom_area = str(a.get("area", ""))
        nom_norm = _sin_tildes(nom_area)
        anios = _cifra_de(a.get("anios")) or 0
        sinonimos = list(_sinonimos_de_area(nom_area)) + _patrones_de_area(nom_norm)

        primera_pos = None

        # 1. Coincidencia por nombre completo del área
        if nom_norm and nom_norm in norm_req:
            pos = norm_req.find(nom_norm)
            primera_pos = pos if primera_pos is None else min(primera_pos, pos)

        # 2. Coincidencia por sinónimos del área
        if sinonimos:
            for pat in sinonimos:
                m = re.search(pat, norm_req)
                if m:
                    pos = m.start()
                    primera_pos = pos if primera_pos is None else min(primera_pos, pos)

        if primera_pos is not None:
            coincidentes.append((a, anios, primera_pos))

    # Ordenar por: mayor cantidad de años (descendente), luego primera aparición en el texto
    coincidentes.sort(key=lambda item: (-item[1], item[2]))
    return coincidentes


def extraer_tokens_nucleo(puesto: str, base_candidato: str) -> List[str]:
    puesto_norm = _sin_tildes(puesto)
    base_norm = _sin_tildes(base_candidato)
    tokens_puesto = re.findall(r"\b[a-z0-9]+\b", puesto_norm)
    candidatos = [w for w in tokens_puesto if len(w) >= 4 and w not in ROLES_Y_STOPWORDS]
    nucleo = []
    for c in candidatos:
        # Check if candidate token appears as a whole word in base_norm
        if not re.search(rf"\b{re.escape(c)}\b", base_norm):
            if c not in nucleo:
                nucleo.append(c)
    return nucleo


def cargar_base_candidato(candidato_dir: Optional[Union[str, Path]] = None) -> str:
    """Base núcleo = experiencia.txt más TODOS los campos de competencias_expandidas.json."""
    base = Path(candidato_dir) if candidato_dir is not None else DATA_DIR / "candidato"
    partes = []
    exp_file = base / "experiencia.txt"
    if exp_file.exists():
        try:
            partes.append(exp_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    comp_file = base / "competencias_expandidas.json"
    if comp_file.exists():
        try:
            datos = json.loads(comp_file.read_text(encoding="utf-8"))
            partes.append(json.dumps(datos, ensure_ascii=False))
        except Exception:
            pass
    return "\n".join(partes)


def aplicar_reglas(
    requisitos: List[Dict[str, Any]],
    puesto: str,
    areas: List[Dict[str, Any]],
    base_candidato: Union[str, List[str]],
) -> List[Dict[str, Any]]:
    """Aplica reglas determinísticas sin IA sobre la lista de requisitos.

    Pura, devuelve una copia profunda sin mutar las entradas.
    Nunca toca requisitos con aprendido=True ni altera eliminatorio_usuario.
    """
    if not isinstance(requisitos, list):
        return []

    reqs = copy.deepcopy(requisitos)
    thematic_areas = _areas_tematicas(areas or [])

    if isinstance(base_candidato, list):
        base_candidato_str = "\n".join(str(x) for x in base_candidato)
    else:
        base_candidato_str = str(base_candidato or "")

    tokens_nucleo = extraer_tokens_nucleo(puesto or "", base_candidato_str)

    for r in reqs:
        if not isinstance(r, dict):
            continue

        # 1. Decisiones manuales / aprendizaje mandan: nunca tocar aprendido=True
        if r.get("aprendido") is True:
            continue

        nombre = r.get("nombre", "")
        texto = r.get("texto", "")
        req_cadena = f"{nombre} {texto}".strip()
        req_norm = _sin_tildes(req_cadena)

        # 2. Logística primero: marcar logistico=true, conservar estado y no endurecer
        if r.get("logistico") or es_requisito_logistico(nombre) or es_requisito_logistico(texto):
            r["logistico"] = True
            continue

        estado = r.get("estado", "")
        tipo = r.get("tipo", "")

        anios_triggered = False
        anios_justif = None
        nucleo_triggered = False
        cert_triggered = False

        # 3. Evaluar Años no respaldados
        n_anios = _extraer_anios(nombre)
        if n_anios is None and texto:
            n_anios = _extraer_anios(texto)

        if n_anios is not None:
            coincidentes = _buscar_areas_coincidentes(req_cadena, thematic_areas)
            if coincidentes:
                mejor_area, m_anios, _ = coincidentes[0]
                nom_area = mejor_area.get("area", "")
                if m_anios < n_anios:
                    anios_triggered = True
                    anios_justif = f"Pide {n_anios} años; el perfil tiene {m_anios} en {nom_area}"
            else:
                # Sin coincidencia de área temática:
                # "sin coincidencia MATCH_DIRECTO se conserva, TRANSFERIBLE pasa a brecha."
                if estado in ("TRANSFERIBLE", "BRECHA_REAL"):
                    anios_triggered = True
                    anios_justif = f"Pide {n_anios} años; sin años en esa área"

        # 4. Evaluar Núcleo (herramienta principal del puesto que el candidato nunca usó)
        if tokens_nucleo:
            for t in tokens_nucleo:
                if re.search(rf"\b{re.escape(t)}\b", req_norm):
                    nucleo_triggered = True
                    token_nucleo = t
                    break

        # 5. Evaluar Certificación
        if _RE_CERT.search(req_norm):
            cert_triggered = True

        # Precedencia: anios > nucleo > cert (conservar anios ante superposición)
        if anios_triggered:
            r["estado"] = "BRECHA_REAL"
            r["tipo"] = "REQUISITO_EXCLUYENTE"
            r["approved"] = False
            r["dura"] = "anios"
            if anios_justif:
                r["justificacion"] = anios_justif
        elif nucleo_triggered:
            # La justificación del modelo contaba por qué era transferible: se reemplaza por el motivo real
            motivo_nucleo = f"El puesto es de «{token_nucleo}» y no figura en el perfil"
            # "Núcleo solo degrada TRANSFERIBLE no RESPONSABILIDAD; puede marcar brecha existente."
            if estado == "TRANSFERIBLE" and tipo != "RESPONSABILIDAD":
                r["estado"] = "BRECHA_REAL"
                r["approved"] = False
                if r.get("dura") != "anios":
                    r["dura"] = "nucleo"
                    r["justificacion"] = motivo_nucleo
            elif estado == "BRECHA_REAL":
                if r.get("dura") != "anios":
                    r["dura"] = "nucleo"
                    r["justificacion"] = motivo_nucleo
        elif cert_triggered:
            # "Certificación solo marca brecha."
            if r.get("estado") == "BRECHA_REAL":
                if r.get("dura") not in ("anios", "nucleo"):
                    r["dura"] = "cert"

    return reqs
