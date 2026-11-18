"""Confirmación conservadora de restricciones. No modifica análisis ni estados.

El archivo se relee para cada análisis nuevo. Para actualizar uno persistido, el
consumidor debe recalcular motivos con la configuración vigente y reemplazar (o
quitar) no_apto; editar el archivo por sí solo no modifica registros históricos.
Una decisión eliminatorio_usuario=False prevalece sobre la autodetección;
la ausencia de decisión permite detectar EDAD/UBICACION sin marca del modelo.
True explícito del creador fuerza el motivo de una brecha excluyente, aunque
la evidencia no confirme la regla automática; el modelo no tiene esa autoridad.
"""
import json
import logging
import functools
import re
import unicodedata
from datetime import date

from core.paths import DATA_DIR

logger = logging.getLogger(__name__)
RESTRICCIONES_FILE = DATA_DIR / "candidato" / "restricciones.json"
_PERMISOS = ("viajes_frecuentes_o_interior", "reubicacion", "rubro_o_funcion_exigida_sin_experiencia")


def _normalizar(texto):
    # Separar CamelCase antes de pasar a minúsculas (p. ej. #EntreRíos).
    texto = re.sub(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])", " ", texto).replace("#", "")
    return " ".join("".join(c for c in unicodedata.normalize("NFD", texto.lower())
                            if unicodedata.category(c) != "Mn").split())


def _validas(restricciones):
    if not isinstance(restricciones, dict):
        return False
    for campo in _PERMISOS:
        if campo in restricciones and type(restricciones[campo]) is not bool:
            return False
    for campo in ("base", "presencial_hibrido_permitido", "remoto_desde"):
        if campo in restricciones and (not isinstance(restricciones[campo], str) or not restricciones[campo].strip()):
            return False
    for campo_lugares in ("residencia", "residencia_cerca_de"):
        if campo_lugares not in restricciones:
            continue
        lugares = restricciones[campo_lugares]
        if (not isinstance(lugares, list) or len(lugares) > MAX_LOCALIDADES
                or any(not isinstance(l, str) or not l.strip() for l in lugares)):
            return False
    if "localidades_permitidas" in restricciones:
        localidades = restricciones["localidades_permitidas"]
        if (not isinstance(localidades, list) or len(localidades) > MAX_LOCALIDADES
                or any(not isinstance(l, str) or not l.strip() for l in localidades)):
            return False
    if "edad" in restricciones:
        edad = restricciones["edad"]
        if not isinstance(edad, dict) or type(edad.get("actual")) is not int or not 0 < edad["actual"] < 130:
            return False
        try:
            if not isinstance(edad.get("al"), str):
                return False
            if date.fromisoformat(edad["al"]) > date.today():
                return False
        except ValueError:
            return False
    return True


def cargar_restricciones() -> dict:
    """Relee la configuración editable; ante errores no inventa restricciones."""
    try:
        restricciones = json.loads(RESTRICCIONES_FILE.read_text(encoding="utf-8"))
        if not _validas(restricciones):
            raise ValueError("tipos de restricciones inválidos")
        return restricciones
    except (OSError, ValueError) as exc:
        logger.warning("No se pudieron cargar las restricciones: %s", exc)
        return {}


_NO_EXIGIDO = re.compile(
    r"\b(?:no|sin|deseabl\w*|valorabl\w*|preferent\w*|opcional\w*|optativ\w*|"
    r"eventual\w*|ocasional\w*|no excluyente|se valora\w*|un plus)\b"
)
_VIAJES = re.compile(r"\b(?:viaj(?:ar|es?|ando)|trasladarse al interior)\b")
_REUBICACION = re.compile(r"\b(?:reubic\w*|mud(?:arse|anza)|radicarse)\b")
MAX_LOCALIDADES = 200


def _re_localidades(localidades) -> "re.Pattern | None":
    """Zonas donde el candidato acepta presencial o híbrido (Mi perfil, «localidades_permitidas»)."""
    frases = sorted({_normalizar(l) for l in localidades or [] if isinstance(l, str) and l.strip()}, key=len, reverse=True)
    return _compilar_localidades(tuple(frases)) if frases else None


@functools.lru_cache(maxsize=8)
def _compilar_localidades(frases: tuple) -> "re.Pattern":
    return re.compile(r"\b(?:" + "|".join(re.escape(f) for f in frases) + r")\b")


# Lista positiva: desconocido nunca equivale a fuera de la zona del candidato. No geocodifica.
_EXTERIOR = re.compile(
    r"\b(?:cordoba|rosario|santa fe|mendoza|neuquen|salta|tucuman|jujuy|chaco|"
    r"chubut|rio negro|santa cruz|tierra del fuego|misiones|corrientes|formosa|"
    r"entre rios|la pampa|san luis|san juan|catamarca|la rioja|santiago del estero|"
    r"mar del plata|bahia blanca|tandil|chile|santiago|uruguay|montevideo|"
    r"brasil|peru|colombia|mexico|espana|paraguay|bolivia|ecuador|venezuela|"
    r"estados unidos|bogota|medellin|cali|barranquilla|lima|quito|guayaquil|caracas|asuncion|la paz|"
    r"ciudad de mexico|cdmx|guadalajara|monterrey|madrid|barcelona|sao paulo|rio de janeiro|panama|"
    r"costa rica|miami)\b"
)
# La modalidad debe exigir ese lugar, no describir el origen de la empresa.
# Solo se admiten hasta tres palabras intermedias, sin cruzar puntuación ni
# conectores que atribuyen la ubicación a una empresa, cliente o sede.
_UBICACION_EXPLICITA = re.compile(
    r"\b(?:presencial\w*|hibrid\w*|on-site|onsite|en oficinas?|trabajar|"
    r"resid(?:encia|ir)|radicad\w*|radicacion)\b"
    r"(?:\s+(?!(?:de|para|empresa|compania|cliente|sede|casa|con)\b)\w+){0,3}"
    r"\s+en\s+" + _EXTERIOR.pattern
)
# Ampliación solo con marca del modelo: el lugar sigue ligado al puesto.
_UBICACION_MARCADA = re.compile(
    r"\bubicacion\s+en\s+" + _EXTERIOR.pattern
    + r"|\bbusca\b(?:\s+(?!(?:empresa|compania|cliente|sede)\b)\w+)*\s+en\s+" + _EXTERIOR.pattern
    + r"|\bmodalidad\b(?:(?!\b(?:empresa|compania|cliente|sede)\b).)*?"
    r"\bpresencial\w*\b[\s:.,;-]*(?:en\s+)?" + _EXTERIOR.pattern
)
_HIBRIDO = re.compile(
    r"\b(?:hibrid[oa]s?|hybrid|presencial\s+o\s+remoto|"
    r"[1-4]\s+dias?\s+(?:presenciales?|en\s+la\s+oficina)|"
    r"presencialidad\s+[1-4]\s+dias?)\b"
)
_RESIDENCIA_EXTERNA = re.compile(
    r"\b(?:residir|residencia|radicad[oa]|vivir|domicilio)\s+en\s+" + _EXTERIOR.pattern
)
_VIAJES_INTENSOS = re.compile(r"\b(?:al interior|frecuentes?|constantes?|permanentes?)\b")
_VIAJES_ESPORADICOS = re.compile(r"\b(?:ocasional\w*|eventual\w*|esporadic\w*)\b")
_RUBRO = re.compile(
    r"\b(?:gastronom\w*|restaurantes?|hoteler\w*|salud asistencial|enfermer\w*|"
    r"finanzas corporativas|planeamiento financiero|planificacion financiera|"
    r"control de gestion|presupuesto\w*|forecast|contabilidad|industria automotriz|"
    r"industria farmaceutica|agroindustria|mineria|construccion)\b"
)
_TECNICO = re.compile(
    r"\b(?:docker|kubernetes|python|java|sql|aws|azure|ingles|idiomas?|certifica\w*|"
    r"frances|portugues|aleman|desarrollo|programacion|software|herramienta\w*)\b"
)


# Residencia exigida (creador, 15/11: «RESIDENCIA significa que debo vivir en esa zona»): se compara con donde vive
# el candidato (Mi perfil, «residencia»), nunca con las zonas donde acepta trabajar. Solo cuenta si nombra un lugar
# reconocible (una zona, una localidad de la lista o del exterior): «zonas aledañas» no descarta
_RESIDENCIA_EXIGIDA = re.compile(
    # «residencia familiar ubicada en…» (oferta 11163) es la casa del empleador, no una exigencia
    r"\b(?:resid(?:encia(?!\s+(?:familiar|particular|privada))|ir|ente|entes)|vivir|viva|domicilio|radicad[oa]s?)\b"
    r"(?:\s+\w+){0,2}?\s+(?:en|cerca de)\s+(?P<lugar>[^;.,()\n]+)")
# Un lugar con nombre: «la zona», «zona operativa» o «zona o aledaños» (la del puesto, sin nombrarla) no cuentan
_LUGAR_GENERICO = re.compile(r"\bzonas?\s+(?:norte|sur|oeste|este|centro)\b|\b(?:capital federal|caba|conurbano)\b")
# «CABA o alrededores»: vale si el candidato vive cerca de ese lugar (Mi perfil, «residencia_cerca_de»)
_ALREDEDORES = re.compile(r"\b(?:aledan\w*|alrededor\w*|cercan\w*|limitrof\w*|proximidad\w*)\b")


def residencia_ajena(texto: str, restricciones: dict) -> bool:
    """Si el texto exige vivir en un lugar que no es donde vive el candidato."""
    lugares = restricciones.get("residencia") if isinstance(restricciones, dict) else None
    propios = _re_localidades(lugares)
    if not propios:
        return False
    normalizado = _normalizar(texto or "")
    for clausula in _clausulas(normalizado):
        m = _RESIDENCIA_EXIGIDA.search(clausula)
        if not m or _NO_EXIGIDO.search(clausula) or propios.search(clausula):
            continue
        cerca = _re_localidades(restricciones.get("residencia_cerca_de"))
        if cerca and cerca.search(clausula) and _ALREDEDORES.search(clausula):
            continue
        conocidos = _re_localidades(restricciones.get("localidades_permitidas"))
        lugar = m.group("lugar")
        if _LUGAR_GENERICO.search(lugar) or _EXTERIOR.search(lugar) or (conocidos and conocidos.search(lugar)):
            return True
    return False


def _categoria_por_tema(texto):
    # Una decisión manual no depende de que la condición automática se incumpla.
    if _REUBICACION.search(texto):
        return "REUBICACION"
    if _VIAJES.search(texto):
        return "VIAJES"
    if re.search(r"\bedad\b", texto):
        return "EDAD"
    if _HIBRIDO.search(texto) or re.search(
            r"\b(?:ubicacion|lugar|modalidad|presencial\w*|hibrid\w*|hybrid|"
            r"remot\w*|resid\w*|radicad\w*|domicilio|vivir)\b", texto):
        return "UBICACION"
    return "RUBRO"


def _clausulas(texto):
    # Separar condiciones para que «ocasional» de viajes no exima una mudanza.
    return [c.strip() for c in re.split(
        r"[;\n.!?]|,\s*(?=(?:empresa|puesto|trabajo|modalidad|sede)\b)|"
        r"\b(?:y|pero|con)\s+(?=(?:reubic\w*|mudarse|viaj\w*|puesto|trabajo|modalidad)\b)",
        texto,
    ) if c.strip()]


def _limites_edad(texto, contexto_edad=False):
    if re.search(r"\b(?:experiencia|trayectoria|antiguedad)\b", texto):
        return None, None
    if not contexto_edad and not re.search(r"\bedad\b", texto) and not re.match(r"mayor(?:es)? de\s+\d{1,3}\b", texto):
        return None, None
    rango = re.search(r"\b(?:entre|de)\s+(\d{1,3})\s*(?:y|a|-)\s*(\d{1,3})\b", texto)
    if not rango:
        rango = re.search(r"\b(\d{1,3})\s+a\s+(\d{1,3})\s+anos\b", texto)
    if rango:
        inferior, superior = map(int, rango.groups())
        return (inferior, superior) if inferior <= superior else (None, None)
    superior = re.search(r"\b(?:hasta|maxim[oa]|no mayor(?:es)? de|menor(?:es)? de)\s*:?\s*(\d{1,3})\b", texto)
    # El tope «no mayor de» no puede aportar también un mínimo «mayor de».
    # Conservar el resto permite detectar un mínimo independiente si lo hay.
    texto_minimo = texto[:superior.start()] + " " + texto[superior.end():] if superior else texto
    inferior = re.search(r"\b(?:minim[oa]|desde|mayor(?:es)? de)\s*:?\s*(\d{1,3})\b", texto_minimo)
    minimo = int(inferior[1]) if inferior else None
    maximo = int(superior[1]) if superior else None
    if superior and superior[0].startswith("menor"):
        maximo -= 1
    if inferior and inferior[0].startswith("mayor"):
        minimo += 1
    return minimo, maximo


def _edad_incumplida(texto, restricciones, contexto_edad=False):
    edad = restricciones.get("edad")
    if not edad:
        return False
    al, hoy = date.fromisoformat(edad["al"]), date.today()
    vigente = edad["actual"] + hoy.year - al.year - ((hoy.month, hoy.day) < (al.month, al.day))
    minimo, maximo = _limites_edad(texto, contexto_edad)
    return (minimo is not None and vigente < minimo) or (maximo is not None and vigente > maximo)


def cumple_limite_edad(texto: str, restricciones: dict) -> bool:
    """Permite quitar una brecha de edad únicamente ante un límite cumplido explícito."""
    if not _validas(restricciones) or not restricciones.get("edad") or not isinstance(texto, str):
        return False
    texto = _normalizar(texto)
    if len(_clausulas(texto)) != 1 or _VIAJES.search(texto) or _REUBICACION.search(texto):
        return False
    minimo, maximo = _limites_edad(texto)
    return (minimo is not None or maximo is not None) and not _edad_incumplida(texto, restricciones)


def motivos_descarte(analisis: dict, restricciones: dict) -> list[dict]:
    """Confirma reglas o decisiones del creador, sin E/S ni mutaciones."""
    if not _validas(restricciones) or not restricciones or not isinstance(analisis, dict) or analisis.get("analisis_fallback"):
        return []
    motivos, vistos = [], set()
    requisitos = analisis.get("requisitos")
    if not isinstance(requisitos, list):
        return []
    for req in requisitos:
        if not isinstance(req, dict):
            continue
        if req.get("eliminatorio_usuario") is not False and isinstance(req.get("nombre"), str):
            # Diga lo que diga el modelo (la marcó MATCH_DIRECTO o TRANSFERIBLE en las ofertas 2042 y 11144)
            cita = req.get("texto") if isinstance(req.get("texto"), str) else req["nombre"]
            if residencia_ajena(f"{req['nombre']}. {cita}", restricciones):
                if (req["nombre"], "UBICACION") not in vistos:
                    vistos.add((req["nombre"], "UBICACION"))
                    motivos.append({"requisito": req["nombre"], "categoria": "UBICACION", "texto": cita})
                continue
        if req.get("estado") != "BRECHA_REAL":
            continue
        # «Me descarta» del creador vale en toda brecha (una responsabilidad o un deseable del núcleo del puesto:
        # «reclutamiento masivo», «servicios legales», 15/11); la detección automática, solo en las excluyentes
        if req.get("tipo") != "REQUISITO_EXCLUYENTE" and req.get("eliminatorio_usuario") is not True:
            continue
        # Solo el creador puede vetar o forzar; la marca del modelo es orientativa.
        if req.get("eliminatorio_usuario") is False:
            continue
        nombre, texto = req.get("nombre"), req.get("texto", req.get("nombre"))
        if not isinstance(nombre, str) or not isinstance(texto, str) or not texto.strip():
            continue
        if req.get("eliminatorio_usuario") is True:
            categoria = req.get("categoria_descarte")
            if not categoria:
                sin_decision = {k: v for k, v in req.items() if k != "eliminatorio_usuario"}
                inferidos = motivos_descarte({"requisitos": [sin_decision]}, restricciones)
                categoria = (inferidos[0]["categoria"] if inferidos
                             else _categoria_por_tema(_normalizar(nombre + " " + texto)))
            clave = (nombre, categoria)
            if clave not in vistos:
                vistos.add(clave)
                motivos.append({"requisito": nombre, "categoria": categoria, "texto": texto})
            continue
        marca = req.get("categoria_descarte") if req.get("eliminatorio") is True else None
        fuentes = [(texto, _normalizar(texto)), (nombre, _normalizar(nombre))]
        contexto = " ".join(normalizado for _, normalizado in fuentes)
        hibrido = bool(_HIBRIDO.search(contexto))
        contexto_edad = marca == "EDAD" or bool(re.search(r"\bedad\b", contexto))
        # Si el nombre es una subcadena de la cita, su contexto manda: no
        # permitir que omita «no se requiere» o «deseable» del texto completo.
        if fuentes[1][1] in fuentes[0][1]:
            fuentes = fuentes[:1]
        for fuente, normalizado in fuentes:
            ubicacion_marcada = (marca == "UBICACION" and _UBICACION_MARCADA.search(normalizado)
                                 and not _NO_EXIGIDO.search(normalizado))
            for clausula in _clausulas(normalizado):
                # «no mayor de» es un tope numérico, no una negación de la exigencia.
                sin_tope = re.sub(r"\bno mayor(?:es)? de\b", "hasta", clausula)
                if _NO_EXIGIDO.search(sin_tope):
                    continue
                categorias = []
                if _edad_incumplida(clausula, restricciones, contexto_edad) and not re.search(r"\b(?:experiencia|trayectoria|antiguedad)\b", normalizado):
                    categorias.append("EDAD")
                # La zona sale solo del perfil: sin localidades permitidas no se descarta por ubicación.
                locales = _re_localidades(restricciones.get("localidades_permitidas"))
                if hibrido:
                    # Un híbrido en otro lugar, aun en otro país, no alcanza: puede aceptar remoto 100 %
                    # para extranjeros (creador, 16/11). Solo descarta si exige residir allá.
                    lugar_exigido = (_RESIDENCIA_EXTERNA.search(clausula)
                                     and not re.search(r"\b(?:empresa|compania|cliente|sede)\b", clausula))
                else:
                    lugar_exigido = _UBICACION_EXPLICITA.search(clausula) or ubicacion_marcada
                if locales and lugar_exigido:
                    # La zona del candidato junto a un lugar externo puede describir la sede de la
                    # empresa o varias ubicaciones: no confirma una exigencia externa.
                    if not locales.search(contexto) and (hibrido or not re.search(r"\bremot\w*\b", contexto)):
                        categorias.append("UBICACION")
                porcentaje = any(int(m[1]) >= 50 for m in re.finditer(r"(?<![\d.,-])(\d{1,3})\s*%", clausula))
                if (restricciones.get("viajes_frecuentes_o_interior") is False and _VIAJES.search(clausula)
                        and not _VIAJES_ESPORADICOS.search(normalizado)
                        and (marca == "VIAJES" or _VIAJES_INTENSOS.search(clausula) or porcentaje)):
                    categorias.append("VIAJES")
                if marca == "REUBICACION" and restricciones.get("reubicacion") is False and _REUBICACION.search(clausula):
                    categorias.append("REUBICACION")
                if marca == "RUBRO" and restricciones.get("rubro_o_funcion_exigida_sin_experiencia") is False:
                    experiencia = re.search(r"\b(?:experiencia|antecedentes|trayectoria|anos en|anos de|haber trabajado)\b", clausula)
                    dominio = _RUBRO.search(clausula) or re.search(r"\b(?:rubro|sector|industria|funcion)\b", clausula)
                    if experiencia and dominio and not _TECNICO.search(clausula):
                        categorias.append("RUBRO")
                for categoria in categorias:
                    clave = (nombre, categoria)
                    if clave not in vistos:
                        vistos.add(clave)
                        motivos.append({"requisito": nombre, "categoria": categoria, "texto": fuente})
    return motivos
