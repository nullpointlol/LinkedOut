"""Subagente 1: Estratega & Clasificador de Brechas (Gap & Transferability Tagger)."""
import contextlib
import copy
import json
import re
import unicodedata
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from core.afinidad_reglas import aplicar_reglas, cargar_base_candidato
from core.aprendizajes import aplicar_aprendizajes_a_requisitos, aprendizajes_para_prompt
from core.bridge import AntigravityBridge, radar_agy_cwd
from core import candidato_datos
from core.cv_guardas import cargar_conocimientos_ausentes, es_requisito_logistico
from core.descartes import cargar_restricciones, cumple_limite_edad, motivos_descarte
from core.expander import CapabilityExpanderAgent
from core.paths import DATA_DIR
from core.perfil_areas import anios_por_area_en_cache
from core.prompts_evaluacion import REGLA_DATOS_DE_TERCEROS, REGLA_RESTRICCIONES, delimitar_datos_de_terceros, prompt_estratega

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
EXP_PATH = DATA_DIR / "candidato" / "experiencia.txt"
PER_PATH = DATA_DIR / "candidato" / "personales.txt"


BENEFIT_OR_BLURB_KEYWORDS = [
    "prepaga", "salario", "sueldo",
    "vacaciones", "bono", "refrigerios", "frutas", "gimnasio", "descuentos",
    "acerca de la empresa", "sobre la empresa", "quiénes somos", "somos una compañía",
    "nuestra misión", "nuestra visión", "beneficios", "compensaciones", "perks",
    "clases de inglés", "in-company"
]


# Requisitos genéricos (actitudes, logística, ofimática): no definen si el candidato sirve para el rol, así que
# pesan poco («Administrativo/a» daba 96 por «Excel», «jornada completa», «perfil organizado»).
_RE_REQUISITO_GENERICO = re.compile(
    r"\b(?:perfil|actitud|proactiv\w*|organizad\w*|responsab(?:le|ilidad)\w*|comunicaci\w*|trabajo en equipo|"
    r"empat\w*|curiosidad|iniciativa|interes|orientaci\w* a|pensamiento|capacidad(?:es)? de|habilidades|"
    r"disponibilidad|jornada|full ?time|part ?time|horario|modalidad|presencial|remot\w*|hibrid\w*|"
    r"residencia|radicaci\w*|zona|movilidad|secundario|paquete office|office|excel|ingles|idioma\w*)\b"
)
PESO_GENERICO = 0.25
PESO_DESEABLE = 0.5
VALOR_TRANSFERIBLE_APROBADO = 0.70
# Transferible que el usuario desaprobó a mano: crédito parcial
VALOR_TRANSFERIBLE = 0.4


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower()) if unicodedata.category(c) != "Mn")


def peso_requisito(tag: Dict[str, Any]) -> float:
    # Residencia, modalidad, jornada o cómo postularse no miden si el candidato sirve: no suman afinidad
    # (un post de 91 caracteres daba 100% con «Residencia» y «Envío de CV» en match)
    if tag.get("logistico") or es_requisito_logistico(tag.get("nombre", "")):
        return 0.0
    estado = (tag.get("estado") or "").upper()
    tipo = (tag.get("tipo") or "").upper()
    if estado == "BRECHA_REAL" and tipo == "REQUISITO_EXCLUYENTE":
        return 2.0 if tag.get("dura") else 1.5
    if _RE_REQUISITO_GENERICO.search(_sin_tildes(tag.get("nombre", ""))):
        return PESO_GENERICO
    return PESO_DESEABLE if tipo == "REQUISITO_DESEABLE" else 1.0


def calculate_match_score_with_breakdown(tags: List[Dict[str, Any]]) -> Tuple[float, Dict[str, Any]]:
    """Calcula el Match Score honesto (0-100%) y el desglose de afinidad con la ruta al 100%.

    Promedio ponderado: logística pesa 0; brechas excluyentes duras 2,0; brechas excluyentes comunes 1,5;
    luego genéricos 0,25, deseables 0,5 y responsabilidades y demás excluyentes 1.
    Match directo vale 1, transferible aprobado 0,70 y transferible no aprobado 0,40.
    Limitado por tope = max(0, 100 - 12 * D) donde D cuenta brechas duras no logísticas con estado BRECHA_REAL.
    Las entradas que no son dict (un análisis dañado) se ignoran."""
    tags = [t for t in (tags or []) if isinstance(t, dict)]
    if not tags:
        return 0.0, {
            "puntos_faltantes": 100.0,
            "brechas": [],
            "transferibles": [],
            "camino_al_100": "No se detectaron requerimientos para evaluar afinidad."
        }

    peso_total = sum(peso_requisito(t) for t in tags) or 1.0
    weighted_sum = 0.0
    brechas = []
    transferibles = []

    for tag in tags:
        nombre = tag.get("nombre", "").strip() or "Requisito sin nombre"
        estado = (tag.get("estado") or "").upper()
        approved = tag.get("approved", True if estado == "MATCH_DIRECTO" else False)
        peso = peso_requisito(tag)
        pts_per_req = 100.0 * peso / peso_total

        if estado == "MATCH_DIRECTO":
            weighted_sum += peso
        elif estado == "TRANSFERIBLE":
            valor = VALOR_TRANSFERIBLE_APROBADO if approved else VALOR_TRANSFERIBLE
            weighted_sum += peso * valor
            deduccion = pts_per_req * (1.0 - valor)
            transferibles.append({
                "nombre": nombre,
                "approved": bool(approved),
                "puntos_deducidos": round(deduccion, 1)
            })
        elif estado == "BRECHA_REAL":
            deduccion = pts_per_req * 1.0
            item = {
                "nombre": nombre,
                "puntos_deducidos": round(deduccion, 1)
            }
            if tag.get("dura"):
                item["dura"] = tag["dura"]
            brechas.append(item)
        else:
            if approved:
                weighted_sum += peso
            else:
                deduccion = pts_per_req * 1.0
                item = {
                    "nombre": nombre,
                    "puntos_deducidos": round(deduccion, 1)
                }
                if tag.get("dura"):
                    item["dura"] = tag["dura"]
                brechas.append(item)

    promedio = (weighted_sum / peso_total) * 100.0
    d_count = sum(
        1 for t in tags
        if not (t.get("logistico") or es_requisito_logistico(t.get("nombre", "")))
        and t.get("dura")
        and (t.get("estado") or "").upper() == "BRECHA_REAL"
    )
    tope = max(0.0, 100.0 - 12.0 * d_count)
    score = round(max(0.0, min(promedio, tope)), 1)
    puntos_faltantes = round(max(0.0, 100.0 - score), 1)

    brechas_nombres = [b["nombre"] for b in brechas]
    transferibles_nombres = [t["nombre"] for t in transferibles]

    if score >= 100.0 or puntos_faltantes == 0.0:
        camino_al_100 = "Perfil 100% alineado con los requerimientos."
    elif brechas_nombres and transferibles_nombres:
        camino_al_100 = f"Para alcanzar el 100%, falta validar: {', '.join(brechas_nombres)} o certificar {', '.join(transferibles_nombres)} en experiencia directa."
    elif brechas_nombres:
        camino_al_100 = f"Para alcanzar el 100%, falta validar o adquirir experiencia en: {', '.join(brechas_nombres)}."
    elif transferibles_nombres:
        camino_al_100 = f"Para alcanzar el 100%, certificar competencias transferibles en experiencia directa: {', '.join(transferibles_nombres)}."
    else:
        camino_al_100 = f"Para alcanzar el 100%, falta cubrir {puntos_faltantes}% de los requerimientos."

    breakdown = {
        "puntos_faltantes": puntos_faltantes,
        "brechas": brechas,
        "transferibles": transferibles,
        "camino_al_100": camino_al_100
    }
    return score, breakdown


def calculate_match_score(tags: List[Dict[str, Any]]) -> float:
    """Calcula el Match Score dinámico honesto sin techo artificial (alcanza el 100.0% si todos los requisitos hacen match directo)."""
    score, _ = calculate_match_score_with_breakdown(tags)
    return score


class StrategistAgent:
    """Subagente Estratega: Extrae dolores de la empresa y etiqueta requisitos en 3 estados."""

    def __init__(
        self,
        bridge: Optional[AntigravityBridge] = None,
        expander: Optional[CapabilityExpanderAgent] = None,
        effort: Optional[str] = None,
    ):
        self.bridge = bridge or AntigravityBridge(effort=effort)
        if effort and hasattr(self.bridge, "effort") and self.bridge.effort != effort:
            self.bridge.effort = effort
        self.effort = effort or getattr(self.bridge, "effort", "low")
        self.expander = expander or CapabilityExpanderAgent()

    def load_candidate_experience(self) -> str:
        """Carga la experiencia y datos personales del candidato."""
        exp = EXP_PATH.read_text(encoding="utf-8") if EXP_PATH.exists() else ""
        per = PER_PATH.read_text(encoding="utf-8") if PER_PATH.exists() else ""
        return f"{per}\n\n{exp}".strip()

    async def analyze_offer(
        self, offer_text: str, effort: Optional[str] = None, datos_de_terceros: bool = False,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Ingesta la oferta laboral y clasifica requerimientos frente a la experiencia del candidato.

        `datos_de_terceros=True` (ofertas del Radar LinkedIn): la oferta va delimitada en <post> y
        el prompt aclara que es contenido de terceros, no instrucciones (anti prompt injection).
        """
        eff = effort or self.effort or getattr(self.bridge, "effort", "low")
        restricciones = cargar_restricciones()
        candidato_info = self.load_candidate_experience()
        expanded_comps = self.expander.get_expanded_competencies()
        expanded_text = ""
        if expanded_comps:
            expanded_lines = [
                f"- {c.get('nombre')} ({c.get('categoria', 'General')}, {c.get('nivel', 'Intermedio')}): {c.get('evidencia', 'Conocimientos aplicados')}"
                for c in expanded_comps
            ]
            expanded_text = "\n".join(expanded_lines)

        if datos_de_terceros:
            bloque_oferta = (
                "OFERTA LABORAL A ANALIZAR (post de LinkedIn de terceros):\n"
                f"{delimitar_datos_de_terceros(offer_text, 'post')}\n\n"
                f"{REGLA_DATOS_DE_TERCEROS.format(tag='post')}"
            )
        else:
            # Flujo manual: exactamente el bloque de siempre
            bloque_oferta = f'OFERTA LABORAL A ANALIZAR:\n"""\n{offer_text}\n"""'

        prompt = f"""
{prompt_estratega()}

{REGLA_RESTRICCIONES}

RESTRICCIONES EDITABLES DEL CANDIDATO:
{json.dumps(restricciones, ensure_ascii=False)}

EXPERIENCIA REAL DEL CANDIDATO:
\"\"\"
{candidato_info}
\"\"\"

COMPETENCIAS EXPANDIDAS Y VALIDADAS DEL CANDIDATO:
\"\"\"
{expanded_text if expanded_text else "Sin competencias adicionales registradas."}
\"\"\"

APRENDIZAJES CONFIRMADOS POR EL CANDIDATO (los marcó a mano en otras ofertas; clasificá igual cualquier requisito equivalente aunque esté redactado distinto):
\"\"\"
{aprendizajes_para_prompt() or "Sin aprendizajes registrados."}
\"\"\"

{bloque_oferta}

INSTRUCCIÓN CLAVE SOBRE COMPETENCIAS EXPANDIDAS:
Si un requisito coincide o se cubre con las COMPETENCIAS EXPANDIDAS Y VALIDADAS DEL CANDIDATO, debes clasificarlo como "MATCH_DIRECTO" (o "TRANSFERIBLE" con approved: true) e indicar en la justificación la evidencia aportada en las competencias expandidas, transformando brechas previas en afinidad real confirmada.

Responde ÚNICAMENTE un objeto JSON válido con las claves:
- "es_oferta": bool
- "empresa": str
- "puesto": str
- "dolores_empresa": list[str]
- "requisitos": list[{{"nombre": str, "tipo": "RESPONSABILIDAD"|"REQUISITO_EXCLUYENTE"|"REQUISITO_DESEABLE", "estado": "MATCH_DIRECTO"|"TRANSFERIBLE"|"BRECHA_REAL", "justificacion": str, "equivalencia": str|null, "approved": bool}}]
- Cada requisito suma "texto": str (cita literal), "eliminatorio": bool; "categoria_descarte" solo cuando eliminatorio es true, según la regla de restricciones.
"""
        causa = ""
        try:
            extra = {"timeout_seconds": timeout_seconds} if timeout_seconds else {}
            # Con datos de terceros, agy corre en un directorio vacío: sin el repo ni los datos al alcance
            with (radar_agy_cwd() if datos_de_terceros else contextlib.nullcontext()) as cwd:
                if cwd:
                    extra["cwd"] = cwd
                result = await self.bridge.run_prompt(prompt, json_mode=True, effort=eff, **extra)
            if isinstance(result, dict) and "requisitos" in result:
                result["es_oferta"] = False if result.get("es_oferta") is False else True
                tags = result.get("requisitos", [])
                for t in tags:
                    # El proveedor no puede atribuirse una decisión del usuario.
                    t.pop("eliminatorio_usuario", None)
                    t.pop("aprendido", None)
                    if t.get("estado") == "TRANSFERIBLE":
                        # Transferible identificado por el modelo: cuenta solo, sin aprobación del usuario
                        # (aprobar a mano obliga a regenerar CV y mail)
                        t["approved"] = True
                    elif "approved" not in t:
                        t["approved"] = True if t.get("estado") == "MATCH_DIRECTO" else False
                    if "tipo" not in t:
                        name_lower = t.get("nombre", "").lower()
                        if any(w in name_lower for w in ("relevamiento", "automatización", "mantenimiento", "diseño", "coordinación")):
                            t["tipo"] = "RESPONSABILIDAD"
                        elif t.get("estado") in ("TRANSFERIBLE", "BRECHA_REAL"):
                            t["tipo"] = "REQUISITO_DESEABLE"
                        else:
                            t["tipo"] = "REQUISITO_EXCLUYENTE"
                # Lo que el usuario ya confirmó en otra oferta no vuelve a aparecer como brecha
                aplicar_aprendizajes_a_requisitos(tags)
                areas = anios_por_area_en_cache()
                base_candidato = cargar_base_candidato()
                tags = aplicar_reglas(tags, puesto=result.get("puesto", ""), areas=areas, base_candidato=base_candidato)
                result["requisitos"] = tags
                # Confirmar sobre citas presentes en la oferta, incluyendo el contexto
                # inmediato: una subcadena no puede ocultar «no requiere» o «deseable».
                evidencia = copy.deepcopy(result)
                verificados = []
                for original, t in zip(tags, evidencia.get("requisitos", [])):
                    if isinstance(t.get("eliminatorio_usuario"), bool):
                        verificados.extend(motivos_descarte({**evidencia, "requisitos": [t]}, restricciones))
                        continue
                    texto = t.get("texto", t.get("nombre", ""))
                    if not isinstance(texto, str) or not texto.strip():
                        continue
                    posiciones = list(re.finditer(re.escape(texto), offer_text))
                    if not posiciones:
                        continue
                    if not result.get("analisis_fallback") and cumple_limite_edad(texto, restricciones):
                        original.update(estado="MATCH_DIRECTO", approved=True, eliminatorio=False)
                        original.pop("categoria_descarte", None)
                        continue
                    # Con múltiples apariciones, exigir que todas confirmen: no
                    # elegir solo la cláusula favorable a un descarte.
                    contextos = []
                    for coincidencia in posiciones:
                        inicio = max(offer_text.rfind(sep, 0, coincidencia.start()) for sep in "\n;.!?") + 1
                        finales = [pos for sep in "\n;.!?" if (pos := offer_text.find(sep, coincidencia.end())) >= 0]
                        fin = min(finales) if finales else len(offer_text)
                        contextos.append(offer_text[inicio:fin].strip())
                    cita = motivos_descarte({**evidencia, "requisitos": [t]}, restricciones)
                    confirmados = [cita] + [motivos_descarte({**evidencia, "requisitos": [{**t, "texto": contexto}]}, restricciones)
                                   for contexto in contextos]
                    comunes = set.intersection(*({m["categoria"] for m in motivos} for motivos in confirmados))
                    for motivo in confirmados[0]:
                        if motivo["categoria"] in comunes:
                            verificados.append({**motivo, "texto": texto})
                result.pop("no_apto", None)
                if verificados:
                    # La función pura ya deduplica por requisito/categoría; aquí
                    # también pueden repetirse requisitos devueltos por el proveedor.
                    unicos = {(m["requisito"], m["categoria"]): m for m in verificados}
                    result["no_apto"] = {"motivos": list(unicos.values())}
                score, desglose = calculate_match_score_with_breakdown(tags)
                result["match_score"] = score
                result["desglose_afinidad"] = desglose
                return result
            else:
                logger.error(f"[StrategistAgent] Respuesta LLM sin estructura de requisitos esperada: {result}")
        except Exception as e:
            logger.error(f"[StrategistAgent] Error ejecutando análisis con LLM: {e}", exc_info=True)
            causa = str(e)

        # Fallback determinístico de contingencia solo si agy/LLM falla de forma irrecuperable
        logger.warning("[StrategistAgent] Activando fallback determinístico de contingencia.")
        resultado = self._deterministic_fallback(offer_text, candidato_info, expanded_comps)
        # Marca para quien necesite distinguirlo (el Radar lo trata como análisis fallido)
        resultado["analisis_fallback"] = True
        if causa:
            # Por qué falló la IA (timeout, 503…): el Radar la conserva en el error del post
            resultado["causa_fallback"] = causa
        resultado.pop("no_apto", None)
        return resultado

    def _deterministic_fallback(
        self,
        offer_text: str,
        candidate_exp: str,
        expanded_competencies: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Extracción determinística exhaustiva procesando viñetas y omitiendo beneficios."""
        if expanded_competencies is None:
            expanded_competencies = self.expander.get_expanded_competencies()

        raw_lines = [l.strip() for l in offer_text.splitlines() if l.strip()]
        puesto = raw_lines[0] if raw_lines else "Posición Técnica"
        stack = candidato_datos.stack()
        empresa = "Empresa Confidencial"
        if len(raw_lines) > 1 and len(raw_lines[1]) < 50 and not raw_lines[1].startswith(("•", "-", "*")):
            empresa = raw_lines[1]

        candidato_lower = candidate_exp.lower()
        requisitos: List[Dict[str, Any]] = []

        current_tipo = "REQUISITO_EXCLUYENTE"
        for line in raw_lines:
            line_lower = line.lower()

            # Detección de secciones
            if any(h in line_lower for h in ("responsabilidades", "funciones", "tareas principales")):
                current_tipo = "RESPONSABILIDAD"
                continue
            elif any(h in line_lower for h in ("requisitos deseables", "deseable", "valoraremos", "plus")):
                current_tipo = "REQUISITO_DESEABLE"
                continue
            elif any(h in line_lower for h in ("requisitos excluyentes", "requisitos", "qué buscamos", "perfil buscado")):
                current_tipo = "REQUISITO_EXCLUYENTE"
                continue
            elif any(b in line_lower for b in ("beneficios", "compensaciones", "acerca de la empresa", "sobre la empresa", "quiénes somos")):
                current_tipo = "IGNORE"
                continue

            if current_tipo == "IGNORE":
                continue

            # Descartar líneas que coinciden con beneficios corporativos o intros
            if any(b in line_lower for b in BENEFIT_OR_BLURB_KEYWORDS):
                continue

            # Detectar si es una viñeta o línea de contenido sustantivo
            is_bullet = line.startswith(("•", "-", "*", "·")) or re.match(r"^\d+[\.\)]", line)
            clean_line = re.sub(r"^[•\-*·\d\.\)\s]+", "", line).strip()

            if len(clean_line) < 6:
                continue

            # Extraer etiqueta sintética concisa
            # Tomar la primera cláusula antes de punto, dos puntos o punto y coma
            first_clause = re.split(r"[\.:;–—]", clean_line)[0].strip()
            words = first_clause.split()
            tag_name = " ".join(words[:6]) if len(words) > 6 else first_clause

            # Clasificación contra el perfil del candidato
            estado = "BRECHA_REAL"
            justif = "Requisito sin antecedentes explícitos directos."
            equiv = None
            matched = False
            tech_found = None

            # 0. Chequeo de Competencias Expandidas
            if expanded_competencies:
                for comp in expanded_competencies:
                    c_name = comp.get("nombre", "").strip()
                    if not c_name:
                        continue
                    if c_name.lower() in line_lower or c_name.lower() in clean_line.lower():
                        estado = "MATCH_DIRECTO"
                        evidencia = comp.get("evidencia", "").strip()
                        nivel = comp.get("nivel", "Intermedio")
                        justif = f"Competencia expandida validada ({nivel}): {evidencia}" if evidencia else f"Competencia expandida validada ({nivel})"
                        matched = True
                        tech_found = c_name
                        break

            # 1. Match directo y 2. transferibles: solo lo que el candidato declaró en su stack
            # (Mi perfil, stack.json). Sin stack no se valida nada por palabra.
            if not matched:
                for fila in stack["coincidencias"]:
                    if any(p.lower() in line_lower for p in fila["palabras"]):
                        estado = "MATCH_DIRECTO"
                        justif = fila["justificacion"]
                        matched = True
                        tech_found = fila["nombre"]
                        break
            if not matched:
                for fila in stack["transferibles"]:
                    if any(p.lower() in line_lower for p in fila["palabras"]):
                        estado = "TRANSFERIBLE"
                        justif = f"Equivalencia comprobable con {fila['equivalente']}"
                        equiv = fila["equivalente"]
                        matched = True
                        tech_found = fila["nombre"]
                        break

            if tech_found and tech_found.lower() not in tag_name.lower():
                tag_name = f"{tag_name} [{tech_found}]"

            requisitos.append({
                "nombre": tag_name,
                "tipo": current_tipo,
                "estado": estado,
                "justificacion": justif,
                "equivalencia": equiv,
                "approved": True if estado == "MATCH_DIRECTO" else False,
            })

        # Si no hubo viñetas: lo que la oferta menciona del stack del candidato y de lo que no tiene
        if len(requisitos) < 3:
            tech_catalog = (
                [(f["nombre"], f["palabras"], "MATCH_DIRECTO", f["justificacion"], None) for f in stack["coincidencias"]]
                + [(f["nombre"], f["palabras"], "TRANSFERIBLE", f"Equivalente a {f['equivalente']}", f["equivalente"])
                   for f in stack["transferibles"]]
                + [(nombre, [nombre], "BRECHA_REAL", "Sin antecedentes en el perfil del candidato", None)
                   for nombre in cargar_conocimientos_ausentes()]
            )
            for tech, palabras, est, just, equivalente in tech_catalog:
                palabras = [p.lower() for p in palabras]
                if any(w in offer_text.lower() for w in palabras):
                    # Verificar si existe como competencia expandida
                    matching_exp = None
                    if expanded_competencies:
                        for c in expanded_competencies:
                            c_nom = str(c.get("nombre") or "").strip().lower()
                            if not c_nom:
                                continue  # sin nombre no valida nada («'' in tech» siempre da verdadero)
                            if any(w in c_nom for w in palabras) or c_nom in tech.lower():
                                matching_exp = c
                                break
                    if matching_exp:
                        est = "MATCH_DIRECTO"
                        evidencia = matching_exp.get("evidencia", "").strip()
                        just = f"Competencia expandida validada: {evidencia}" if evidencia else f"Competencia expandida validada ({matching_exp.get('nombre')})"

                    requisitos.append({
                        "nombre": tech,
                        "tipo": "REQUISITO_EXCLUYENTE",
                        "estado": est,
                        "justificacion": just,
                        "equivalencia": equivalente if est == "TRANSFERIBLE" else None,
                        "approved": True if est == "MATCH_DIRECTO" else False,
                    })

            # Añadir competencias expandidas adicionales si figuran en offer_text
            if expanded_competencies:
                for c in expanded_competencies:
                    c_nom = c.get("nombre", "").strip()
                    if c_nom and c_nom.lower() in offer_text.lower():
                        if not any(c_nom.lower() in r["nombre"].lower() for r in requisitos):
                            evidencia = c.get("evidencia", "").strip()
                            requisitos.append({
                                "nombre": c_nom,
                                "tipo": "REQUISITO_EXCLUYENTE",
                                "estado": "MATCH_DIRECTO",
                                "justificacion": f"Competencia expandida validada: {evidencia}" if evidencia else f"Competencia expandida validada ({c_nom})",
                                "equivalencia": None,
                                "approved": True,
                            })

        aplicar_aprendizajes_a_requisitos(requisitos)
        areas = anios_por_area_en_cache()
        base_candidato = cargar_base_candidato()
        requisitos = aplicar_reglas(requisitos, puesto=puesto, areas=areas, base_candidato=base_candidato)
        score, desglose = calculate_match_score_with_breakdown(requisitos)

        return {
            "es_oferta": True,
            "empresa": empresa,
            "puesto": puesto,
            "dolores_empresa": [
                "Reducción sistemática de tareas manuales operativas y aceleración de entregas.",
                "Consolidación de calidad de código y observabilidad de incidentes en tiempo real.",
                "Articulación fluida entre requerimientos de negocio y equipos técnicos."
            ],
            "requisitos": requisitos,
            "match_score": score,
            "desglose_afinidad": desglose,
        }


EstrategaAgent = StrategistAgent
