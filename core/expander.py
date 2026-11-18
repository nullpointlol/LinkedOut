"""Módulo expand: Cuestionario / Entrevista Asistida para Enriquecer Perfil Profesional."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

from core.bridge import AntigravityBridge
from core.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_EXPANDED_PATH = DATA_DIR / "candidato" / "competencias_expandidas.json"


class CapabilityExpanderAgent:
    """Agente de Descubrimiento y Expansión de Competencias Profesionales Latentes."""

    def __init__(
        self,
        bridge: Optional[AntigravityBridge] = None,
        storage_path: Optional[Union[str, Path]] = None,
    ):
        self.bridge = bridge or AntigravityBridge()
        self.storage_path = Path(storage_path) if storage_path else DEFAULT_EXPANDED_PATH

    def get_expanded_competencies(self) -> List[Dict[str, Any]]:
        """Lee y retorna la lista de competencias expandidas desde el almacenamiento seguro."""
        if not self.storage_path.exists():
            try:
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                self.storage_path.write_text("[]", encoding="utf-8")
            except Exception:
                pass
            return []

        try:
            raw = self.storage_path.read_text(encoding="utf-8").strip()
            if not raw:
                return []
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    def confirm_competencies(self, confirmed_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Valida, sanitiza, persiste y deduplica competencias por nombre (case-insensitive)."""
        current_list = self.get_expanded_competencies()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for item in confirmed_items:
            name = str(item.get("nombre", "")).strip()
            if not name:
                continue

            category = str(item.get("categoria", "")).strip() or self._infer_category(name)
            level = str(item.get("nivel", "")).strip() or "Intermedio"
            evidence = str(item.get("evidencia", "")).strip()
            created_at = item.get("created_at") or now_str

            sanitized_entry = {
                "nombre": name,
                "categoria": category,
                "nivel": level,
                "evidencia": evidence,
                "created_at": created_at,
            }

            # Buscar coincidencia existente case-insensitive
            existing_idx = next(
                (i for i, c in enumerate(current_list) if c.get("nombre", "").strip().lower() == name.lower()),
                -1,
            )

            if existing_idx >= 0:
                # Actualizar conservando la fecha de creación original si existía
                original_created = current_list[existing_idx].get("created_at")
                if original_created:
                    sanitized_entry["created_at"] = original_created
                current_list[existing_idx] = sanitized_entry
            else:
                current_list.append(sanitized_entry)

        # Persistir a disco
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text(json.dumps(current_list, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[CapabilityExpanderAgent] Error guardando competencias: {e}")

        return current_list

    def remove_competency(self, name_or_index: Union[str, int]) -> bool:
        """Elimina una competencia por índice numérico o por nombre exacto/case-insensitive."""
        current_list = self.get_expanded_competencies()
        if not current_list:
            return False

        idx_to_remove = -1

        # Caso 1: Entero o string puramente numérico
        if isinstance(name_or_index, int) or (isinstance(name_or_index, str) and name_or_index.isdigit()):
            idx = int(name_or_index)
            if 0 <= idx < len(current_list):
                idx_to_remove = idx

        # Caso 2: Búsqueda por nombre
        if idx_to_remove == -1 and isinstance(name_or_index, str):
            target = name_or_index.strip().lower()
            for i, c in enumerate(current_list):
                if c.get("nombre", "").strip().lower() == target:
                    idx_to_remove = i
                    break

        if idx_to_remove != -1:
            current_list.pop(idx_to_remove)
            try:
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                self.storage_path.write_text(json.dumps(current_list, indent=2, ensure_ascii=False), encoding="utf-8")
                return True
            except Exception:
                return False

        return False

    async def generate_discovery_questions(
        self, target_role: str, missing_keywords: List[str]
    ) -> List[Dict[str, Any]]:
        """Formula preguntas socráticas mediante LLM o fallback determinístico."""
        cleaned_keywords = [k.strip() for k in missing_keywords if k and k.strip()]
        if not cleaned_keywords:
            return []

        prompt = f"""Eres un consultor de reclutamiento técnico senior y orientador de carrera.
El candidato está postulándose a la posición: '{target_role or 'Profesional Técnico'}'.
La vacante solicita las siguientes competencias o tecnologías que actualmente no figuran explícitas en su CV formal:
{json.dumps(cleaned_keywords, ensure_ascii=False)}

Formula una pregunta socrática, constructiva y reflexiva para cada tecnología con el fin de descubrir si el candidato tiene experiencia latente, proyectos personales, laboratorios prácticos o conocimiento adyacente.

Responde ÚNICAMENTE un objeto JSON válido con la estructura:
{{
  "preguntas": [
    {{
      "keyword": "Nombre exacto de la competencia",
      "pregunta": "¿Has trabajado con ...?",
      "categoria_sugerida": "Herramientas" | "Metodologías" | "Infraestructura" | "Procesos" | "Habilidades Blandas",
      "sugerencia_evidencia": "Ejemplo concreto de evidencia que el candidato podría aportar"
    }}
  ]
}}
"""

        try:
            result = await self.bridge.run_prompt(prompt, json_mode=True)
            if isinstance(result, dict):
                if "preguntas" in result and isinstance(result["preguntas"], list) and result["preguntas"]:
                    return result["preguntas"]
                if "items" in result and isinstance(result["items"], list) and result["items"]:
                    return result["items"]
            elif isinstance(result, list) and result:
                return result
        except Exception:
            pass

        return self._deterministic_questions(target_role, cleaned_keywords)

    def _infer_category(self, keyword: str) -> str:
        """Infiere la categoría adecuada para una tecnología o habilidad."""
        kw_lower = keyword.lower()
        infra_kw = {"docker", "kubernetes", "k8s", "aws", "gcp", "azure", "cloud", "linux", "terraform", "ansible", "ci/cd", "jenkins"}
        metod_kw = {"scrum", "kanban", "agile", "lean", "safe", "cascada", "itil"}
        procesos_kw = {"relevamiento", "bpmn", "uml", "historias de usuario", "especificaciones", "casos de uso", "diagramación"}
        soft_kw = {"liderazgo", "comunicación", "negociación", "resolución de conflictos", "gestión del tiempo"}

        if any(w in kw_lower for w in infra_kw):
            return "Infraestructura"
        if any(w in kw_lower for w in metod_kw):
            return "Metodologías"
        if any(w in kw_lower for w in procesos_kw):
            return "Procesos"
        if any(w in kw_lower for w in soft_kw):
            return "Habilidades Blandas"
        return "Herramientas"

    def _deterministic_questions(
        self, target_role: str, keywords: List[str]
    ) -> List[Dict[str, Any]]:
        """Generador determinístico de preguntas socráticas."""
        questions: List[Dict[str, Any]] = []
        role_label = target_role or "la posición"

        for kw in keywords:
            cat = self._infer_category(kw)
            if cat == "Infraestructura":
                q_text = f"¿Has tenido contacto, configuración básica o despliegue de pruebas con {kw} en proyectos personales o laborales?"
                ev_sug = f"Despliegues locales o administración básica de {kw}"
            elif cat == "Metodologías":
                q_text = f"¿Has participado en ceremonias, gestión de tableros o seguimiento de tareas bajo {kw}?"
                ev_sug = f"Participación activa en equipos ágiles con {kw}"
            elif cat == "Procesos":
                q_text = f"¿Has realizado documentación, relevamiento funcional o especificaciones técnicas vinculadas a {kw}?"
                ev_sug = f"Documentación y modelado técnico de {kw}"
            else:
                q_text = f"¿Has utilizado o integrado {kw} para resolver algún desafío práctico o automatización en relación a {role_label}?"
                ev_sug = f"Uso práctico o integración de {kw} en proyectos técnicos"

            questions.append({
                "keyword": kw,
                "pregunta": q_text,
                "categoria_sugerida": cat,
                "sugerencia_evidencia": ev_sug,
            })

        return questions
