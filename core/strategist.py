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
from core.cv_guardas import es_requisito_logistico
from core.descartes import cargar_restricciones, cumple_limite_edad, motivos_descarte
from core.expander import CapabilityExpanderAgent
from core.paths import DATA_DIR
from core.perfil_areas import anios_por_area_en_cache
from core.prompts_evaluacion import REGLA_DATOS_DE_TERCEROS, REGLA_RESTRICCIONES, delimitar_datos_de_terceros, prompt_estratega

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
EXP_PATH = DATA_DIR / "candidato" / "experiencia.txt"
PER_PATH = DATA_DIR / "candidato" / "personales.txt"


class AnalisisSinIA(RuntimeError):
    """El modelo no devolvió un análisis válido; no hay análisis determinista."""


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
- "ubicacion": {{"pais": str|null, "provincia": str|null, "localidad": str|null, "modalidad": "presencial"|"hibrida"|"remota"|null}} (dónde se trabaja según la oferta; la provincia normalizada, p. ej. «Zona Norte, Córdoba» → provincia Córdoba)
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
                    if cumple_limite_edad(texto, restricciones):
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

        # Sin IA la tarea falla con su causa; sin cuota `run_prompt` espera y no llega acá
        raise AnalisisSinIA("el modelo no respondió un análisis válido" + (f" ({causa})" if causa else ""))


EstrategaAgent = StrategistAgent
