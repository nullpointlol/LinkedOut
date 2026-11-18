"""Contrato B: calidad fija y benchmark de auditoría (no del flujo CV + mail).

Corpus fijo: cuatro CV castellanos limpios y seis con tres keywords ausentes.
Base medida: 4 * 1 + 6 * 5 = 34 llamadas. Contrato B: 4 * 1 + 6 * 2 = 16.
INVARIANT_CHECK lo configura el loop; ningún test inicia workers ni proveedores.
"""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from contextvars import ContextVar
import os
import re
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader

from core.auditor import AuditorAgent, TIMEOUT_REMEDIACION_S, scan_anti_ai_writing
from core.bridge import AntigravityBridge
from core.idioma import EN, ES, idioma_de_cv, seccion_canonica
from core.pdf_generator import compile_markdown_to_pdf
from tests.test_cv_formatting_and_a4_e2e import SAMPLE_E2E_CV, KEYWORDS, EXPECTED_COMPANIES
from tests.test_idioma_cv import CV_EN


# Capturada durante colección, antes de que conftest instale su stub autouse.
REVISION_REAL = AuditorAgent.revisar_senales_ia
CV_ES = SAMPLE_E2E_CV.rstrip("\n")
CV_INGLES = CV_EN.rstrip("\n")
FALTANTES = ["Kubernetes", "Terraform", "Rust"]
KW_FALTANTES = [*KEYWORDS, *FALTANTES]
PITCH = "Hola equipo, les comparto mi postulación como Analista Funcional Técnico."
DEDUCCION_KW = "-13.3 pts en Keywords: faltan 3 keywords clave (Kubernetes, Terraform, Rust)."
DEDUCCION_EN = (
    "-15.0 pts en Impacto & Métricas: detectada solo 1 evidencia empírica "
    "(cuantitativas o de alcance operativo) (requeridas >=3)."
)
SENAL = {"frase": "trayectoria de 7 años", "patron": "plantilla controlada",
         "reescritura": "práctica de 7 años"}
SENAL_EXP = {"frase": "Automaticé el seguimiento de más de 90 aplicaciones",
             "patron": "plantilla controlada", "reescritura": ""}
GUARDA = 60  # solo guarda contra bloqueos (con la PC cargada 5 s no alcanzaban)


class BridgeContado:
    effort = "low"
    last_telemetry = None

    def __init__(self, respuesta=None, senales=()):
        self.llamadas = []
        self.respuesta = respuesta or {"lineas": [], "pitch": ""}
        self.senales = list(senales)
        self.run_prompt = AsyncMock(side_effect=self._responder)

    async def _responder(self, prompt, **kwargs):
        self.llamadas.append((prompt, kwargs))
        if kwargs.get("effort") == "low":
            return {"senales": [s for s in self.senales if s["frase"] in prompt]}
        if callable(self.respuesta):
            return await self.respuesta(prompt, **kwargs)
        return self.respuesta

    @property
    def revisiones(self):
        return [p for p, kw in self.llamadas if kw.get("effort") == "low"]

    @property
    def remediaciones(self):
        return [p for p, kw in self.llamadas if kw.get("effort") == "medium"]


@pytest.fixture
def lente_real(monkeypatch):
    monkeypatch.setattr(AuditorAgent, "revisar_senales_ia", REVISION_REAL)


def numero(texto, fragmento):
    return next(n for n, linea in enumerate(texto.splitlines()) if fragmento in linea)


def editables(prompt):
    """Sólo los n absolutos ofrecidos por el request, no números del contexto."""
    return {int(n): texto for n, texto in re.findall(r"^\s*(\d+): (.*)$", prompt, re.M)}


def secciones_editables(cv):
    resultado = {}
    seccion = None
    for n, linea in enumerate(cv.splitlines()):
        if linea.startswith("## "):
            seccion = seccion_canonica(linea)
        elif seccion and linea.strip() and not linea.startswith("#"):
            resultado[n] = seccion
    return resultado


def informe_fijo(report, *, ingles=False, faltantes=False):
    """invariante: valores literales medidos, incluyendo hallazgos y deducciones."""
    approved = not faltantes
    deducciones = [DEDUCCION_KW] if faltantes else ([DEDUCCION_EN] if ingles else [])
    hallazgos = {
        "keywords_faltantes": FALTANTES if faltantes else [],
        "palabras_ai": [], "empresas_faltantes": [],
        "lista_observaciones": [
            "Integrar 3 keywords aprobadas faltantes: Kubernetes, Terraform, Rust."
        ] if faltantes else [],
        "requiere_accion": not approved,
    }
    assert report["approved"] is approved
    assert report["verdict"] == ("PASE A ENTREVISTA" if approved else "REQUIERE REMEDIACIÓN")
    assert report["ats_score"] == (86.7 if faltantes else 85 if ingles else 100)
    assert report["global_score"] == (90.7 if faltantes else 89.5 if ingles else 100)
    assert report["anti_ai_score"] == 100
    assert report["anti_ai"]["clean"] is True
    assert report["anti_ai"]["detected_words"] == []
    assert report["deducciones"] == deducciones
    assert report["ats"]["deducciones"] == deducciones
    assert report["hallazgos_unificados"] == hallazgos
    assert report["diagnostico_documento"]["hallazgos_unificados"] == hallazgos
    assert report["evaluacion_profesional"]["hallazgos_unificados"] == hallazgos
    assert report["evaluacion_profesional"]["approved"] is approved
    assert report["evaluacion_profesional"]["deducciones"] == deducciones
    assert report["ats"]["missing_keywords"] == (FALTANTES if faltantes else [])
    assert report["ats"]["missing_sections"] == []
    assert report["ats"]["missing_experiences"] == []
    assert report["ats"]["has_all_sections"] is True
    assert report["ats"]["has_all_experiences"] is True
    assert report["ats"]["keyword_coverage"] == (0.62 if faltantes else 1.0)


def pdf_fiel(cv, base, empresas, tmp_path, nombre):
    """invariante: PDF real de una página A4; trayectoria y títulos del idioma."""
    path = compile_markdown_to_pdf(cv, output_filename=str((tmp_path / nombre).resolve()))
    reader = PdfReader(str(path))
    assert len(reader.pages) == 1
    pagina = reader.pages[0]
    assert float(pagina.mediabox.width) == pytest.approx(595.3, abs=2)
    assert float(pagina.mediabox.height) == pytest.approx(841.9, abs=2)
    texto = " ".join(pagina.extract_text().split())
    assert idioma_de_cv(cv) == idioma_de_cv(base)
    for empresa in empresas:
        assert empresa in texto
    for titulo in (l[3:] for l in base.splitlines() if l.startswith("## ")):
        assert "## " + titulo in cv
        assert titulo in texto
    # La empresa sola no basta: todas las viñetas de experiencia deben sobrevivir.
    en_experiencia = False
    for linea in base.splitlines():
        if linea.startswith("## "):
            en_experiencia = seccion_canonica(linea) == "EXPERIENCIA"
        if en_experiencia and linea.startswith("•"):
            assert linea in cv
            assert " ".join(linea[1:].replace("**", "").split()) in texto


@asynccontextmanager
async def tareas_controladas(hijas=()):
    tareas = []

    def lanzar(coro):
        tarea = asyncio.create_task(coro)
        tareas.append(tarea)
        return tarea

    try:
        yield lanzar
    finally:
        # El test también contiene una implementación defectuosa que abandone hijas.
        pendientes = list(dict.fromkeys([*tareas, *hijas]))
        for tarea in pendientes:
            if not tarea.done():
                tarea.cancel()
        await asyncio.gather(*pendientes, return_exceptions=True)


async def esperar(awaitable, mensaje="El trabajo no terminó ni drenó sus hijas"):
    try:
        return await asyncio.wait_for(awaitable, timeout=GUARDA)
    except asyncio.TimeoutError:
        pytest.fail(mensaje)


async def turnos():
    for _ in range(4):
        evento = asyncio.Event()
        asyncio.get_running_loop().call_soon(evento.set)
        await evento.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["castellano", "ingles", "keywords", "senal", "empresa"])
async def test_invariantes_hallazgos_trayectoria_idioma_y_a4(lente_real, tmp_path, caso):
    ingles = caso == "ingles"
    cv = CV_INGLES if ingles else CV_ES
    empresas = ["Acme"] if ingles else EXPECTED_COMPANIES
    keywords = ["SQL", "Python", "Power BI"] if ingles else (
        KW_FALTANTES if caso == "keywords" else KEYWORDS)
    bridge = BridgeContado(senales=[SENAL] if caso == "senal" else [])
    auditor = AuditorAgent(bridge=bridge)
    # invariante: fixtures públicas sin salto final, longitudes y lengua medidas.
    assert len(cv) == (621 if ingles else 1326)
    assert idioma_de_cv(cv) == (EN if ingles else ES)
    if caso == "empresa":
        cv = cv.replace("KORVA", "Empresa omitida")
    pdf_fiel(cv, cv, [e for e in empresas if caso != "empresa" or e != "KORVA"],
             tmp_path, "antes.pdf")
    salida, pitch, report = await auditor.audit_and_remediate(
        cv, PITCH, keywords, max_attempts=0, expected_companies=empresas)
    # invariante: max_attempts=0 no modifica contenido ni pide remediación.
    assert salida == cv and pitch == PITCH
    assert len(bridge.revisiones) == 1 and bridge.remediaciones == []
    assert bridge.llamadas[0][1]["timeout_seconds"] == 120
    if caso == "senal":
        # invariante: una cita literal controlada bloquea aunque ATS=100.
        palabras = ["«trayectoria de 7 años» (plantilla controlada)"]
        assert report["approved"] is False and report["ats_score"] == 100
        assert report["global_score"] == 95.5 and report["anti_ai_score"] == 85
        assert report["anti_ai"]["senales_llm"] == [SENAL]
        assert report["hallazgos_unificados"] == {
            "keywords_faltantes": [], "palabras_ai": palabras, "empresas_faltantes": [],
            "lista_observaciones": ["Remover clichés y muletillas de IA detectadas: " + palabras[0] + "."],
            "requiere_accion": True,
        }
        assert report["deducciones"] == [
            "-15 pts en Redacción: términos no naturales o faltas ortográficas (" + palabras[0] + ")."]
        assert report["verdict"] == "REQUIERE REMEDIACIÓN"
    elif caso == "empresa":
        # invariante: score alto jamás autoriza un CV sin una empresa esperada.
        assert report["ats_score"] == 99 and report["global_score"] == 79.3
        assert report["approved"] is False
        assert report["ats"]["missing_experiences"] == ["KORVA"]
        assert report["hallazgos_unificados"]["empresas_faltantes"] == ["KORVA"]
        assert report["hallazgos_unificados"]["lista_observaciones"] == [
            "Restaurar empresas obligatorias del historial laboral: KORVA."]
        assert report["deducciones"] == [
            "-20.0 pts en Historial: omisión de empresas esperadas de la trayectoria (KORVA)."]
    else:
        informe_fijo(report, ingles=ingles, faltantes=caso == "keywords")
    pdf_fiel(salida, cv, [e for e in empresas if caso != "empresa" or e != "KORVA"],
             tmp_path, "despues.pdf")
    if os.environ.get("INVARIANT_CHECK") == "1":
        return


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["limpio", "sin_progreso", "aislamiento"])
async def test_una_revision_por_version_y_corte_sin_progreso(lente_real, caso):
    bridge = BridgeContado()
    auditor = AuditorAgent(bridge=bridge)
    cv, pitch, report = await auditor.audit_and_remediate(
        CV_ES, PITCH, KEYWORDS, max_attempts=2, expected_companies=EXPECTED_COMPANIES)
    # invariante: CV aprobado hace una revisión y conserva todos los hallazgos.
    assert cv == CV_ES and pitch == PITCH
    informe_fijo(report)
    assert len(bridge.revisiones) == 1 and bridge.remediaciones == []
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    if caso == "limpio":
        return
    if caso == "aislamiento":
        # defecto: la caché nunca cruza invocaciones, keywords ni candidatos.
        bridge.senales = [SENAL]
        _, _, nuevo = await auditor.audit_and_remediate(
            CV_ES, PITCH, KEYWORDS, max_attempts=0, expected_companies=EXPECTED_COMPANIES)
        assert len(bridge.revisiones) == 2
        assert nuevo["approved"] is False and nuevo["anti_ai"]["senales_llm"] == [SENAL]
        bridge.senales = []
        _, _, nuevo = await auditor.audit_and_remediate(
            CV_ES, PITCH, KW_FALTANTES, max_attempts=0, expected_companies=EXPECTED_COMPANIES)
        informe_fijo(nuevo, faltantes=True)
        assert len(bridge.revisiones) == 3
        return
    bridge.llamadas.clear()
    cv, pitch, report = await auditor.audit_and_remediate(
        CV_ES, PITCH, KW_FALTANTES, max_attempts=2, expected_companies=EXPECTED_COMPANIES)
    informe_fijo(report, faltantes=True)
    assert cv == CV_ES and pitch == PITCH
    # defecto: base=5; una revisión + un intento vacío, sin aprobar lo no corregido.
    assert len(bridge.llamadas) == 2, "Se repite la lente o se reintenta sin progreso (base: 5)"
    assert len(bridge.revisiones) == len(bridge.remediaciones) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["pitch_determinista", "pitch_cero", "solo_pitch", "cv_cambiado", "canales"])
async def test_pitch_cambiado_no_repite_lente_cv(lente_real, caso):
    bridge = BridgeContado()
    auditor = AuditorAgent(bridge=bridge)
    # invariante: compatibilidad determinista medida, sin llamadas ocultas.
    limpio = auditor.remediate_text_deterministic("In summary, a holistic approach.")
    assert limpio == "a end-to-end approach."
    assert scan_anti_ai_writing(limpio)["clean"] is True
    bridge.run_prompt.assert_not_awaited()
    informe_fijo(auditor.audit(CV_ES, PITCH, KEYWORDS, expected_companies=EXPECTED_COMPANIES))
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    if caso in ("pitch_determinista", "pitch_cero"):
        attempts = 0 if caso == "pitch_cero" else 2
        cv, pitch, report = await auditor.audit_and_remediate(
            CV_ES, "In summary, a holistic approach.", KEYWORDS,
            max_attempts=attempts, expected_companies=EXPECTED_COMPANIES)
        assert cv == CV_ES
        assert pitch == ("In summary, a holistic approach." if attempts == 0 else limpio)
        assert report["approved"] is (attempts > 0)
        assert len(bridge.revisiones) == 1 and bridge.remediaciones == []
    elif caso == "solo_pitch":
        # «desarrolló» requiere LLM: no lo cubre la contingencia determinista.
        bridge.respuesta = {"lineas": [], "pitch": PITCH}
        cv, pitch, report = await auditor.audit_and_remediate(
            CV_ES, "El candidato desarrolló informes.", KEYWORDS,
            max_attempts=2, expected_companies=EXPECTED_COMPANIES)
        assert cv == CV_ES and pitch == PITCH
        informe_fijo(report)
        assert len(bridge.revisiones) == len(bridge.remediaciones) == 1
        assert editables(bridge.remediaciones[0]) == {}, "Pitch no habilita líneas del CV"
    elif caso == "cv_cambiado":
        n = numero(CV_ES, "• Análisis funcional")
        linea = CV_ES.splitlines()[n] + " Kubernetes, Terraform, Rust."
        bridge.respuesta = {"lineas": [{"n": n, "texto": linea}], "pitch": ""}
        cv, pitch, report = await auditor.audit_and_remediate(
            CV_ES, PITCH, KW_FALTANTES, max_attempts=2, expected_companies=EXPECTED_COMPANIES)
        assert cv == CV_ES.replace(CV_ES.splitlines()[n], linea) and pitch == PITCH
        assert report == auditor.audit(cv, pitch, KW_FALTANTES, expected_companies=EXPECTED_COMPANIES)
        assert report["approved"] is True
        assert len(bridge.revisiones) == 1 and len(bridge.remediaciones) == 1
        assert CV_ES in bridge.revisiones[0]
    else:
        canales = {"email": "In summary, a holistic approach.", "inmail": "Hola " * 90,
                   "web_ats": "", "otro": "Mensaje natural."}
        cv, pitch, salidas, report = await auditor.audit_and_remediate_all(
            CV_ES, "In summary, a holistic approach.", canales, KEYWORDS,
            max_attempts=2, expected_companies=EXPECTED_COMPANIES)
        assert cv == CV_ES and pitch == limpio
        assert salidas["email"] == limpio and salidas["web_ats"] == ""
        assert salidas["otro"] == canales["otro"] and len(salidas["inmail"]) <= 300
        assert canales["email"] == "In summary, a holistic approach."
        informe_fijo(report)
        assert len(bridge.revisiones) == 1 and bridge.remediaciones == []


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["concurrencia", "orden_completo", "ambito", "conflicto_directo", "conflicto_inverso", "legado"])
async def test_destinos_concurrencia_y_conflictos(lente_real, tmp_path, caso):
    bridge = BridgeContado()
    auditor = AuditorAgent(bridge=bridge)
    # invariante: la base completa tiene las empresas y aprobación esperadas.
    informe_fijo(auditor.audit(CV_ES, PITCH, KEYWORDS, expected_companies=EXPECTED_COMPANIES))
    assert all(e in CV_ES for e in EXPECTED_COMPANIES)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = numero(CV_ES, "Analista Técnico Funcional con trayectoria")
    skills = numero(CV_ES, "• Análisis funcional")
    idioma = numero(CV_ES, "• Español: Nativo")
    bridge.senales = [SENAL]
    if caso == "orden_completo":
        bridge.senales = [SENAL, SENAL_EXP,
                         {"frase": "Licenciatura en Sistemas de Información",
                          "patron": "prueba educación", "reescritura": ""},
                         {"frase": "Español: Nativo | Inglés: C1 Profesional",
                          "patron": "prueba idiomas", "reescritura": ""}]
        await auditor.audit_and_remediate(
            CV_ES, "El candidato desarrolló informes.", KW_FALTANTES,
            max_attempts=1, expected_companies=EXPECTED_COMPANIES)
        assert len(bridge.remediaciones) == 1
        secciones = secciones_editables(CV_ES)
        destinos = {secciones[n] for n in editables(bridge.remediaciones[0])}
        assert destinos == {"HABILIDADES", "PERFIL", "EXPERIENCIA", "EDUCACION", "IDIOMAS"}
        assert "El candidato desarrolló informes." in bridge.remediaciones[0]
        return
    if caso == "concurrencia":
        # Todas las secciones marcadas se consolidan en una sola llamada de remediación.
        bridge.senales = [SENAL, SENAL_EXP]
        experiencia = numero(CV_ES, "• Automaticé el seguimiento")
        cambios = {
            skills: CV_ES.splitlines()[skills] + " Kubernetes, Terraform, Rust.",
            perfil: CV_ES.splitlines()[perfil].replace(SENAL["frase"], SENAL["reescritura"]),
            experiencia: CV_ES.splitlines()[experiencia].replace("el seguimiento", "el monitoreo"),
        }
        bridge.respuesta = {
            "lineas": [{"n": n, "texto": txt} for n, txt in cambios.items()],
            "pitch": "",
        }
        cv, pitch, report = await auditor.audit_and_remediate(
            CV_ES, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES)
        assert len(bridge.remediaciones) == 1
        secciones = secciones_editables(CV_ES)
        assert {secciones[n] for n in editables(bridge.remediaciones[0])} >= {"HABILIDADES", "PERFIL", "EXPERIENCIA"}
        esperado = CV_ES.splitlines()
        for n, texto in cambios.items():
            esperado[n] = texto
        assert cv == "\n".join(esperado)
        assert pitch == PITCH and report["approved"] is True
        assert all(e in cv for e in EXPECTED_COMPANIES)
        assert report == auditor.audit(cv, pitch, KW_FALTANTES,
                                       expected_companies=EXPECTED_COMPANIES,
                                       senales_llm=[s for s in bridge.senales if s["frase"] in cv])
        return
    if caso == "legado":
        # Respuesta antigua: aceptar sólo diferencias verificables del destino.
        bridge.senales = []
        nuevo_skills = CV_ES.splitlines()[skills] + " Kubernetes, Terraform, Rust."
        ajeno = CV_ES.replace(CV_ES.splitlines()[skills], nuevo_skills).replace(
            CV_ES.splitlines()[idioma], "• Francés: Nativo.")
        bridge.respuesta = {"cv_markdown": ajeno, "pitch": "Pitch ajeno"}
        cv, pitch, report = await auditor.audit_and_remediate(
            CV_ES, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES)
        assert cv == CV_ES.replace(CV_ES.splitlines()[skills], nuevo_skills)
        assert pitch == PITCH
        assert report["approved"] is True
        pdf_fiel(cv, CV_ES, EXPECTED_COMPANIES, tmp_path, "legado.pdf")
        return
    bridge.senales = []
    cambios = [
        {"n": idioma, "texto": "• Francés: Nativo."},
        {"n": -1, "texto": "x"}, {"n": 9999, "texto": "x"},
        {"n": numero(CV_ES, "## HABILIDADES"), "texto": "## OTRO"},
        {"n": skills, "texto": "• Kubernetes\n## IDIOMAS\n• Francés"},
        {"n": skills, "texto": "• **Kubernetes"},
        {"n": str(skills), "texto": "• Kubernetes, Terraform, Rust."},
        {"n": skills + 0.5, "texto": "• Kubernetes, Terraform, Rust."},
    ]
    if caso.startswith("conflicto"):
        cambios += [{"n": skills, "texto": "• Kubernetes, Terraform, Rust, SQL."},
                    {"n": skills, "texto": "• Kubernetes, Terraform, Rust, Python."}]
        if caso == "conflicto_inverso":
            cambios.reverse()
    bridge.respuesta = {"lineas": cambios, "pitch": "Pitch no marcado"}
    cv, pitch, report = await auditor.audit_and_remediate(
        CV_ES, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES)
    # defecto: todos los cambios son ajenos, inválidos o incompatibles; nadie gana.
    assert cv == CV_ES, "Aceptó una edición ajena o un ganador entre parches incompatibles"
    assert pitch == PITCH
    informe_fijo(report, faltantes=True)
    assert set(editables(bridge.remediaciones[0])) == {
        n for n, seccion in secciones_editables(CV_ES).items() if seccion == "HABILIDADES"}


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["error", "cancelacion", "global_empresa", "global_idioma", "tope_1", "tope_3"])
async def test_error_cancelacion_y_validacion_global(lente_real, monkeypatch, tmp_path, caso):
    bridge = BridgeContado()
    auditor = AuditorAgent(bridge=bridge)
    # invariante: los rechazos conservan hallazgos incluso con global >90.
    informe_fijo(auditor.audit(CV_ES, PITCH, KW_FALTANTES,
                              expected_companies=EXPECTED_COMPANIES), faltantes=True)
    if os.environ.get("INVARIANT_CHECK") == "1":
        return
    perfil = numero(CV_ES, "Analista Técnico Funcional con trayectoria")
    skills = numero(CV_ES, "• Análisis funcional")
    if caso.startswith("global_"):
        # El bridge heredado ofrece CV sin KORVA o con títulos en otro idioma.
        if caso == "global_empresa":
            malo = re.sub(r"### Desarrollador de Software \| KORVA.*?(?=## HABILIDADES)",
                          "", CV_ES, flags=re.S)
        else:
            malo = CV_ES
            for es, en in [("PERFIL", "SUMMARY"), ("EXPERIENCIA", "EXPERIENCE"),
                           ("HABILIDADES TÉCNICAS", "TECHNICAL SKILLS"),
                           ("EDUCACIÓN", "EDUCATION"), ("IDIOMAS", "LANGUAGES")]:
                malo = malo.replace("## " + es, "## " + en)
        bridge.respuesta = {"cv_markdown": malo, "pitch": PITCH}
        cv, pitch, report = await auditor.audit_and_remediate(
            CV_ES, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES)
        assert report == auditor.audit(cv, pitch, KW_FALTANTES,
                                       expected_companies=EXPECTED_COMPANIES)
        # defecto: validación global y rechazo de cambios fuera del destino.
        assert cv == CV_ES and report["approved"] is False
        pdf_fiel(cv, CV_ES, EXPECTED_COMPANIES, tmp_path, caso + ".pdf")
        return
    if caso.startswith("tope_"):
        capacidad_solicitada = int(caso[-1])
        capacidad = min(2, capacidad_solicitada)
        monkeypatch.setenv("LINKEDOUT_AGY_MAX_CONCURRENTES", str(capacidad_solicitada))
        entradas = asyncio.Queue()
        liberar = asyncio.Event()
        activas = 0
        maximo = 0
        por_padre = Counter()
        max_local = Counter()
        hijas = []
        invocacion = ContextVar("auditoria_sintetica")

        async def ejecutar(self, prompt, json_mode=True, timeout_seconds=180, effort=None, cwd=None):
            nonlocal activas, maximo
            if effort == "low":
                return {"senales": [SENAL]}
            hijas.append(asyncio.current_task())
            # Medir cada invocación sin exigir datos ajenos en el prompt acotado.
            etiqueta = invocacion.get()
            activas += 1
            por_padre[etiqueta] += 1
            maximo = max(maximo, activas)
            max_local[etiqueta] = max(max_local[etiqueta], por_padre[etiqueta])
            entradas.put_nowait(etiqueta)
            try:
                await liberar.wait()
                return {"lineas": [], "pitch": ""}
            finally:
                activas -= 1
                por_padre[etiqueta] -= 1

        monkeypatch.setattr(AntigravityBridge, "_ejecutar_prompt", ejecutar)

        async def auditar_nombre(nombre):
            token = invocacion.set(nombre)
            try:
                return await AuditorAgent(bridge=AntigravityBridge()).audit_and_remediate(
                    CV_ES.replace("SAM ORTEGA", nombre), PITCH, KW_FALTANTES,
                    max_attempts=1, expected_companies=EXPECTED_COMPANIES)
            finally:
                invocacion.reset(token)

        async with tareas_controladas(hijas) as lanzar:
            padres = [lanzar(auditar_nombre(nombre))
                for nombre in ("CANDIDATO_A", "CANDIDATO_B", "CANDIDATO_C")]
            for _ in range(capacidad):
                await esperar(entradas.get())
            await turnos()
            assert activas == capacidad and maximo == capacidad
            assert max(max_local.values()) <= 1
            liberar.set()
            resultados = await esperar(asyncio.gather(*padres))
            assert activas == 0 and maximo <= capacidad
            for cv, pitch, report in resultados:
                assert all(e in cv for e in EXPECTED_COMPANIES)
                assert report["approved"] is False
            if capacidad_solicitada == 3:
                assert max(max_local.values()) == 1
        return
    bridge.senales = [SENAL]
    entradas = asyncio.Queue()
    liberar = asyncio.Event()
    fallar = asyncio.Event()
    activas = set()
    hijas = []
    finalizadas = []

    async def respuesta(prompt, **kwargs):
        tarea = asyncio.current_task()
        hijas.append(tarea)
        activas.add("REMEDIACION")
        entradas.put_nowait("REMEDIACION")
        try:
            if caso == "error":
                await fallar.wait()
                raise RuntimeError("falló remediación")
            await liberar.wait()
            return {"lineas": [{"n": skills, "texto": CV_ES.splitlines()[skills]
                                + " Kubernetes, Terraform, Rust."}], "pitch": ""}
        finally:
            activas.remove("REMEDIACION")
            finalizadas.append("REMEDIACION")

    bridge.respuesta = respuesta
    async with tareas_controladas(hijas) as lanzar:
        padre = lanzar(auditor.audit_and_remediate(
            CV_ES, PITCH, KW_FALTANTES, max_attempts=1, expected_companies=EXPECTED_COMPANIES))
        await esperar(entradas.get())
        assert activas == {"REMEDIACION"}
        if caso == "cancelacion":
            padre.cancel()
            with pytest.raises(asyncio.CancelledError):
                await esperar(padre)
            assert activas == set() and len(finalizadas) == 1
            assert all(t.done() for t in hijas), "El padre volvió con hijas sin drenar"
        else:
            fallar.set()
            cv, pitch, report = await esperar(padre)
            assert activas == set() and all(t.done() for t in hijas)
            assert all(e in cv for e in EXPECTED_COMPANIES)
            assert report["approved"] is False
            assert cv.splitlines()[skills] == CV_ES.splitlines()[skills]
            assert report["ats"]["missing_keywords"] == FALTANTES
            assert "práctica de 7 años" in cv or SENAL["frase"] in cv
            assert report == auditor.audit(cv, pitch, KW_FALTANTES,
                                           expected_companies=EXPECTED_COMPANIES,
                                           senales_llm=[s for s in bridge.senales if s["frase"] in cv])
            pdf_fiel(cv, CV_ES, EXPECTED_COMPANIES, tmp_path, "error.pdf")


@pytest.mark.asyncio
async def test_diez_casos_estres_con_contador(lente_real, record_property):
    # Benchmark de esta etapa.
    corpus = [("limpio_1", KEYWORDS), ("limpio_2", KEYWORDS),
              ("limpio_3", KEYWORDS), ("limpio_4", KEYWORDS),
              ("faltan_1", KW_FALTANTES), ("faltan_2", KW_FALTANTES),
              ("faltan_3", KW_FALTANTES), ("faltan_4", KW_FALTANTES),
              ("faltan_5", KW_FALTANTES), ("faltan_6", KW_FALTANTES)]
    bridge = BridgeContado()
    auditor = AuditorAgent(bridge=bridge)
    conteos = []
    for nombre, keywords in corpus:
        antes = len(bridge.llamadas)
        cv, pitch, report = await auditor.audit_and_remediate(
            CV_ES, PITCH, keywords, max_attempts=2, expected_companies=EXPECTED_COMPANIES)
        # invariante: seis rechazos no se excluyen del denominador ni se aprueban para ahorrar.
        assert cv == CV_ES and pitch == PITCH
        informe_fijo(report, faltantes=nombre.startswith("faltan"))
        conteos.append(len(bridge.llamadas) - antes)
    record_property("auditoria_llamadas_por_caso", conteos)
    record_property("auditoria_llamadas_total", len(bridge.llamadas))
    record_property("auditoria_base_medida", 34)
    # invariante: esfuerzos y tiempos de espera no cambian para ahorrar llamadas.
    for _, kwargs in bridge.llamadas:
        assert kwargs["json_mode"] is True
        assert kwargs["effort"] in ("low", "medium")
        assert kwargs["timeout_seconds"] == (120 if kwargs["effort"] == "low"
                                               else TIMEOUT_REMEDIACION_S)
    if os.environ.get("INVARIANT_CHECK") == "1":
        # Referencia medida del estado base, no una meta de ahorro del flujo completo.
        assert conteos == [1, 1, 1, 1, 5, 5, 5, 5, 5, 5]
        assert len(bridge.llamadas) == 34
        return
    # defecto: mismas decisiones y hallazgos; sólo se elimina trabajo repetido.
    assert conteos == [1, 1, 1, 1, 2, 2, 2, 2, 2, 2], f"Base medida=34, obtenido={conteos}"
    assert len(bridge.llamadas) == 16
