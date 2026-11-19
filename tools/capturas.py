"""Capturas del README y CV de ejemplo, con la persona inventada de `config/ejemplo` (Sam Ortega, desarrolladora web).

Uso: python tools/capturas.py [--puerto 1199]

1. Arma una carpeta de datos temporal: `config/ejemplo/candidato` más los archivos que le faltan, y ofertas
   ficticias de desarrollo web (empresas inventadas, mails `.example`).
2. Levanta el portal en el puerto (sin Radar), con un Gmail inventado para que no aparezca el aviso de «Gmail no
   configurado» (nunca manda nada: no hay Radar y la herramienta no aprueba envíos). Si el puerto está ocupado, aborta.
3. Captura LinkedIn (lista, detalle y mail), Mi perfil y la app a 390, 768 y 1280 px: una pantalla de alto por
   captura, sin avisos de error, PNG optimizado, en `docs/capturas/`.
4. Genera el CV de ejemplo (PDF, con el mismo generador que el portal) en `docs/ejemplo/`.

Nunca lee `data/` ni un `.env`: todo sale de `config/ejemplo` y de lo que arma este archivo. Revisá las imágenes y
el PDF antes de publicarlos, y pasá `python tools/auditar_datos.py --local`.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "docs" / "capturas"
EJEMPLO = RAIZ / "docs" / "ejemplo"
ANCHOS = {"celular": 390, "tablet": 768, "escritorio": 1280}
ALTOS = {390: 844, 768: 1024, 1280: 800}

# Archivos del perfil que config/ejemplo no trae (Mi perfil los muestra; el motor tiene valores por defecto)
EXTRAS = {
    "voz_email.md": "Tono cercano y directo. Tuteo a quien publicó. Sin frases hechas. Hasta 120 palabras.\n",
    "estructura_cv.json": {"cargos_relevantes_entre": 10, "empresas": [{"empresa": "Estudio Ejemplo", "cargos": "todos"}]},
    "aprendizaje_puntaje.json": {"umbral": 60, "min_ofertas": 3},
}
CV_EJEMPLO = """# SAM ORTEGA
## Desarrolladora Frontend
Ciudad Ejemplo, Buenos Aires, Argentina | +54 9 11 0000-0000 | https://wa.me/5491100000000 | sam.ortega@example.com | https://www.linkedin.com/in/sam-ortega-ejemplo/

## PERFIL PROFESIONAL
Desarrolladora web con 10 años de experiencia: interfaces en React y TypeScript, APIs en Node.js y pruebas de punta a punta. Trabajo cerca de diseño y producto para que cada página cargue rápido y se use bien desde el celular.

## EXPERIENCIA LABORAL
### Estudio Ejemplo
### Desarrolladora Frontend | 2021 - 2025
• Construí interfaces en React y TypeScript para una tienda online, con componentes accesibles y probados con Jest.
• Medí el rendimiento de cada página con Lighthouse y bajé el tiempo de carga de la portada.
• Revisé código de otras personas del equipo en GitHub antes de cada pase a producción.
### Desarrolladora Web | 2018 - 2021
• Programé APIs REST en Node.js con Express y PostgreSQL para los sitios de los clientes del estudio.
• Escribí pruebas de punta a punta con Playwright para los flujos de compra.
### Agencia Demo
### Maquetadora Web | 2015 - 2018
• Maqueté sitios en HTML y CSS a partir de diseños en Figma, adaptados a celulares.

## HABILIDADES TÉCNICAS
• **Frontend:** React, TypeScript, JavaScript, HTML, CSS.
• **Backend y datos:** Node.js, Express, PostgreSQL, APIs REST.
• **Calidad:** Jest, Playwright, Lighthouse.
• **Herramientas:** Git, GitHub Actions, Figma.

## EDUCACIÓN
• Tecnicatura en Programación - Instituto de Ejemplo (2012 - 2015)

## IDIOMAS
• Español: Nativo
• Inglés: Intermedio
"""

# Siembra (corre en otro proceso, con LINKEDOUT_DATA_DIR apuntando a la carpeta temporal)
SEMBRAR = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import server  # noqa: F401  (crea la base)
from core import radar_store
from core.database import create_postulacion, update_postulacion
CV = json.loads(sys.argv[2])

def analisis(puesto, empresa, score, requisitos, dolores):
    return {"es_oferta": True, "empresa": empresa, "puesto": puesto, "match_score": score,
            "dolores_empresa": dolores, "requisitos": requisitos}

def req(nombre, tipo, estado, justificacion):
    return {"nombre": nombre, "tipo": tipo, "estado": estado, "approved": estado != "BRECHA_REAL",
            "justificacion": justificacion}

OFERTAS = [
    ("Desarrolladora Frontend Sr.", "Tienda Ejemplo", "LISTO", 86, "talento@tiendaejemplo.example", [
        req("React", "REQUISITO_EXCLUYENTE", "MATCH_DIRECTO", "4 años en Estudio Ejemplo"),
        req("TypeScript", "REQUISITO_EXCLUYENTE", "MATCH_DIRECTO", "Componentes tipados"),
        req("Pruebas con Jest", "RESPONSABILIDAD", "MATCH_DIRECTO", "Componentes probados con Jest"),
        req("Next.js", "REQUISITO_DESEABLE", "TRANSFERIBLE", "React en producción"),
        req("Experiencia en e-commerce", "REQUISITO_DESEABLE", "MATCH_DIRECTO", "Tienda online")],
     ["Lanzar la tienda nueva antes de fin de año", "Mejorar la accesibilidad del sitio"]),
    ("Full Stack Developer (remoto LATAM)", "Agencia Norte Ejemplo", "ANALIZADA", 78, "rrhh@agencianorte.example", [
        req("Node.js", "REQUISITO_EXCLUYENTE", "MATCH_DIRECTO", "APIs REST con Express"),
        req("PostgreSQL", "REQUISITO_EXCLUYENTE", "MATCH_DIRECTO", "Sitios de clientes"),
        req("Docker", "REQUISITO_DESEABLE", "BRECHA_REAL", "No figura")],
     ["Sumar a alguien que cubra front y back"]),
    ("Desarrolladora Mobile Jr.", "Fintech Demo", "ENVIADO", 74, "jobs@fintechdemo.example", [
        req("React Native", "REQUISITO_EXCLUYENTE", "TRANSFERIBLE", "React en la web")],
     ["Primera app para celulares"]),
    ("Diseñadora UX/UI", "Estudio Sur Ejemplo", "ANALIZADA", 64, "hola@estudiosur.example", [
        req("Figma", "REQUISITO_EXCLUYENTE", "MATCH_DIRECTO", "Maquetado desde Figma"),
        req("Investigación con usuarios", "REQUISITO_DESEABLE", "BRECHA_REAL", "No figura")],
     ["Ordenar el sistema de diseño"]),
]
for i, (puesto, empresa, estado, score, mail, requisitos, dolores) in enumerate(OFERTAS):
    texto = (f"¡Estamos buscando {puesto}! Sumate a {empresa}.\n\nQué vas a hacer:\n- {dolores[0]}\n\n"
             f"Requisitos:\n" + "".join(f"- {r['nombre']}\n" for r in requisitos) +
             f"\nModalidad híbrida (Centro, Ciudad Ejemplo). Enviá tu CV a {mail}")
    pid = radar_store.insert_post_if_new({
        "urn": f"urn:li:activity:{8100 + i}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{8100 + i}/",
        "autor": f"Selección {empresa}", "cargo_autor": "Talent Acquisition", "fecha_rel": f"{i + 2} h",
        "texto": texto, "emails": [mail]}, "Frontend")
    campos = {"estado": estado, "analisis": analisis(puesto, empresa, score, requisitos, dolores), "canal": "email",
              "triage": {"oferta": 1, "puesto": puesto, "empresa": empresa, "canal": "email", "alcance": "ARGENTINA"}}
    if estado in ("LISTO", "ENVIADO"):
        post_id = create_postulacion(puesto, empresa, texto, status="READY", origen="linkedin",
                                     radar_post_ref=f"urn:li:activity:{8100 + i}")
        update_postulacion(post_id, cv_markdown=CV, match_score=score, requisitos=json.dumps(requisitos))
        campos.update(postulacion_id=post_id, email_to=mail, email_subject=f"{puesto} — Sam Ortega",
                      email_body=(f"Hola, equipo de {empresa}:\n\nVi la búsqueda de {puesto} y me interesa. Hace "
                                  "cuatro años construyo interfaces en React y TypeScript para una tienda online, "
                                  "con componentes accesibles y probados.\n\nTe adjunto mi CV. Quedo atenta.\n\n"
                                  "Sam Ortega"))
    if estado == "ENVIADO":
        campos.update(sent_at="2026-11-18 10:30:00", aprobado_at="2026-11-18 10:29:00")
    radar_store.update_post(pid, **campos)
# Una oferta cargada a mano (vista Manual y su editor), con análisis, revisión y CV
manual = create_postulacion("Desarrolladora React (proyecto de 6 meses)", "Logística Ejemplo",
                            "Buscamos Desarrolladora React para el panel de seguimiento de envíos.\n\nRequisitos:\n"
                            "- React y TypeScript\n- Pruebas automatizadas\n- Deseable: mapas en la web",
                            status="READY", origen="manual")
update_postulacion(manual, cv_markdown=CV, match_score=82,
                   dolores_empresa=json.dumps(["Mostrar en vivo dónde está cada envío", "Que el panel ande en celulares"]),
                   requisitos=json.dumps([
                       req("React", "REQUISITO_EXCLUYENTE", "MATCH_DIRECTO", "4 años en Estudio Ejemplo"),
                       req("TypeScript", "REQUISITO_EXCLUYENTE", "MATCH_DIRECTO", "Componentes tipados"),
                       req("Pruebas automatizadas", "RESPONSABILIDAD", "MATCH_DIRECTO", "Jest y Playwright"),
                       req("Mapas en la web", "REQUISITO_DESEABLE", "BRECHA_REAL", "No figura")]),
                   reporte_auditoria=json.dumps({"verdict": "PASE A ENTREVISTA", "ats_score": 92,
                       "ats": {"keyword_coverage": 0.8, "found_keywords": ["React", "TypeScript", "Jest"],
                               "missing_keywords": ["Mapas"]}}))
print("ofertas", len(OFERTAS) + 1)
'''

# CV de ejemplo: el mismo generador que usa el portal (Edge/Chrome sin ventana o ReportLab)
GENERAR_CV = r'''
import json, shutil, sys
sys.path.insert(0, sys.argv[1])
from core.pdf_generator import compile_markdown_to_pdf
# El generador pone el puesto como subtítulo: el «## Desarrolladora Frontend» del Markdown sobraría
cv = json.loads(sys.argv[2]).replace("## Desarrolladora Frontend\n", "", 1)
ruta = compile_markdown_to_pdf(cv, target_role="Desarrolladora Frontend",
                               output_filename="CV_Sam_Ortega_ejemplo.pdf", candidate_name="Sam Ortega")
shutil.copy(ruta, sys.argv[3])
print(ruta.name)
'''


def _puerto_libre(puerto):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", puerto)) != 0


def _datos(carpeta: Path) -> None:
    candidato = carpeta / "candidato"
    shutil.copytree(RAIZ / "config" / "ejemplo" / "candidato", candidato)
    sys.path.insert(0, str(RAIZ))
    from tests.portales_prueba import reglas
    for nombre, contenido in {**EXTRAS, "reglas_portales.json": reglas(
            apagados=("computrabajo", "bumeran", "remoteok", "remotive", "himalayas"),
            jooble={"ubicacion": "Ciudad Ejemplo"}, indeed={"ubicacion": "Ciudad Ejemplo, Argentina"})}.items():
        texto = contenido if isinstance(contenido, str) else json.dumps(contenido, ensure_ascii=False, indent=2) + "\n"
        (candidato / nombre).write_text(texto, encoding="utf-8")


def _entorno(carpeta: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LINKEDOUT_", "GMAIL_"))}
    archivo_env = carpeta / "capturas.env"
    archivo_env.write_text("GMAIL_ADDRESS=sam.ortega@example.com\nGMAIL_APP_PASSWORD=ejemplo-sin-uso\n", encoding="utf-8")
    env.update(LINKEDOUT_DATA_DIR=str(carpeta), LINKEDOUT_SIN_RADAR="1", LINKEDOUT_ENV_FILE=str(archivo_env),
               GMAIL_ADDRESS="sam.ortega@example.com", GMAIL_APP_PASSWORD="ejemplo-sin-uso", PYTHONIOENCODING="utf-8")
    return env


def _optimizar(ruta: Path) -> None:
    """PNG de 256 colores sin tramado (la interfaz es de colores planos): pesa una fracción y se ve igual."""
    from PIL import Image
    imagen = Image.open(ruta).convert("RGB")
    imagen.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(ruta, optimize=True)


def _capturar(base: str) -> list:
    from playwright.sync_api import sync_playwright
    hechas = []

    def foto(page, nombre):
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), f"{nombre}: scroll horizontal"
        visibles = page.evaluate("""() => ['radarGmailAviso', 'radarCuotaAviso', 'radarEnviosPausados', 'radarBusquedasPausadas',
            'statusAlert'].filter(id => { const e = document.getElementById(id); return e && e.offsetParent !== null; })""")
        assert not visibles, f"{nombre}: aviso visible {visibles}"
        ruta = SALIDA / f"{nombre}.png"
        page.screenshot(path=str(ruta))  # una pantalla: el alto del viewport
        _optimizar(ruta)
        hechas.append(ruta)

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        for ancho in ANCHOS.values():
            page = navegador.new_page(viewport={"width": ancho, "height": ALTOS[ancho]})
            errores = []
            page.on("pageerror", lambda e, errores=errores: errores.append(str(e)))
            page.goto(base + "/", wait_until="load")
            page.wait_for_timeout(1500)
            # LinkedIn: lista, detalle y mail (en escritorio, las tres columnas a la vez)
            page.click("#btnVistaRadar")
            page.wait_for_selector("[data-post-id]")
            page.wait_for_timeout(800)
            if ancho < 1024:
                foto(page, f"linkedin-lista-{ancho}")
            page.locator("[data-post-id]").first.click()
            page.wait_for_timeout(1000)
            if ancho < 1024:
                foto(page, f"linkedin-detalle-{ancho}")
                page.click("[data-radar-tab=email]")
                page.wait_for_timeout(800)
                foto(page, f"linkedin-mail-{ancho}")
            else:
                foto(page, f"linkedin-{ancho}")
            # Mi perfil: en el celular, el índice; en tablet y escritorio, «Qué busco»
            page.click("#btnVistaPerfil")
            page.wait_for_timeout(1500)
            if ancho < 1024:
                foto(page, f"perfil-indice-{ancho}")
            page.click(".perfil-ir[data-seccion=busco]")  # en el celular abre la sección; en escritorio, va hasta ella
            page.wait_for_timeout(1200)
            if ancho >= 768:
                foto(page, f"perfil-{ancho}")
            # La app: la postulación con su CV (Manual › editor)
            page.evaluate("cambiarVista('editor')")
            page.wait_for_timeout(800)
            page.locator("#listaOfertasCards > *").first.click()
            page.wait_for_timeout(1200)
            if ancho < 1024:
                page.click("#tabBtn-cv")
                page.wait_for_timeout(400)
            foto(page, f"app-{ancho}")
            assert not errores, errores
            page.close()
        navegador.close()
    return hechas


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--puerto", type=int, default=1199)
    args = ap.parse_args()
    if args.puerto == 1111 or not _puerto_libre(args.puerto):
        sys.exit(f"El puerto {args.puerto} está ocupado (o es producción): elegí otro con --puerto.")
    SALIDA.mkdir(parents=True, exist_ok=True)
    EJEMPLO.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linkedout-capturas-") as tmp:
        carpeta = Path(tmp) / "datos"
        _datos(carpeta)
        env = _entorno(carpeta)
        subprocess.run([sys.executable, "-c", SEMBRAR, str(RAIZ), json.dumps(CV_EJEMPLO)], cwd=RAIZ, env=env, check=True)
        subprocess.run([sys.executable, "-c", GENERAR_CV, str(RAIZ), json.dumps(CV_EJEMPLO),
                        str(EJEMPLO / "CV_Sam_Ortega_ejemplo.pdf")], cwd=RAIZ, env=env, check=True)
        servidor = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--port", str(args.puerto)],
                                    cwd=RAIZ, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{args.puerto}"
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + "/", timeout=2)
                    break
                except OSError:
                    time.sleep(0.5)
            hechas = _capturar(base)
        finally:
            servidor.terminate()
            servidor.wait(timeout=20)
    for ruta in hechas:
        print(f"{ruta.relative_to(RAIZ)}  {ruta.stat().st_size // 1024} KB")
    print(f"{(EJEMPLO / 'CV_Sam_Ortega_ejemplo.pdf').relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
