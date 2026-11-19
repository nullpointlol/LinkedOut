"""Postulaciones enviadas visibles en un navegador real (sesión 5, 11/11).

Con postulaciones enviadas por email y por otros canales, un post descartado con CV y una postulación del Radar
sin post: en la vista Radar el filtro «Enviadas» muestra todas, cada una abre puesto, empresa, destinatario, fecha
de envío, el mail (asunto y cuerpo) y su CV en PDF y DOCX descargables, y la que no tiene post aparece en el
historial de la vista Radar.
"""
import json

import pytest

from core import database, radar_store
from tests.test_pdf_export import SAMPLE_CV
from tests.test_ui_navegador import ANALISIS_FALSO, TEXTO_OFERTA, _abrir, _vista, navegador, portal  # noqa: F401

pytestmark = [pytest.mark.ui, pytest.mark.timeout(300)]

# (canal, destinatario, asunto, cuerpo, fecha de envío)
ENVIADAS = [
    ("email", "rrhh@acme.test", "Analista Funcional - Sam Ortega", "Hola, adjunto mi CV para Acme.", "2026-11-01 10:15:00"),
    ("email", "talento@korva.test", "Analista Funcional - Sam Ortega", "Hola, adjunto mi CV para Korva.", "2026-11-03 18:40:00"),
    ("email", "jobs@velora.test", "Functional Analyst - Sam Ortega", "Hi, please find my CV attached.", "2026-11-09 23:49:00"),
    ("dm", "https://www.linkedin.com/in/selectora-prueba/", "", "Hola, te escribo por la búsqueda.", "2026-11-05 09:00:00"),
]


def _sembrar_enviadas():
    posts = []
    for i, (canal, destino, asunto, cuerpo, enviado) in enumerate(ENVIADAS):
        empresa = f"Empresa {i}"
        pid = radar_store.insert_post_if_new({
            "urn": f"urn:li:activity:{8800 + i}", "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{8800 + i}/",
            "autor": f"Selección {i}", "cargo_autor": "Talent Acquisition", "fecha_rel": "2 sem",
            "texto": f"{TEXTO_OFERTA} (aviso {i})", "emails": [destino] if canal == "email" else [],
        }, "consulta de prueba")
        postulacion = database.create_postulacion(f"Analista Funcional {i}", empresa, "texto", status="READY",
                                                  origen="linkedin", radar_post_ref=f"urn:li:activity:{8800 + i}")
        database.update_postulacion(postulacion, cv_markdown=SAMPLE_CV)
        analisis = dict(json.loads(json.dumps(ANALISIS_FALSO)), empresa=empresa, puesto=f"Analista Funcional {i}")
        campos = {"estado": "ENVIADO", "canal": canal, "postulacion_id": postulacion, "analisis": analisis,
                  "triage": {"oferta": 1, "puesto": f"Analista Funcional {i}", "canal": canal},
                  "email_body": cuerpo, "sent_at": enviado, "message_id": f"<{i}@prueba>" if canal == "email" else f"manual:{canal}"}
        if canal == "email":
            campos.update(email_to=destino, email_subject=asunto)
        else:
            campos.update(destino_canal=destino)
        radar_store.update_post(pid, **campos)
        posts.append((pid, postulacion))
    return posts


def _fecha_vista(fecha):
    """«2026-11-09 23:49:00» → «09/11 23:49», como la muestra el portal."""
    dia, hora = fecha.split(" ")
    _, mes, d = dia.split("-")
    return f"{d}/{mes} {hora[:5]}"


@pytest.mark.parametrize("ancho", (375, 1200))
def test_cada_enviada_se_ve_y_abre_su_mail_y_su_cv(navegador, portal, ancho):
    base, _ = portal
    posts = _sembrar_enviadas()
    contexto, page, errores = _abrir(navegador, base, ancho)
    try:
        _vista(page, "radar")
        page.click("#radarEstadoBtn")  # los estados se eligen desde el selector (creador, 19/11)
        page.click('#vista-radar button[data-estado="ENVIADO"]')
        page.wait_for_timeout(400)
        tarjetas = page.evaluate("[...document.querySelectorAll('#radarLista [data-post-id], #radarListaFeed [data-post-id]')]"
                                 ".map(e => Number(e.dataset.postId))")
        assert sorted(tarjetas) == sorted(pid for pid, _ in posts)
        assert f"Enviadas {len(ENVIADAS)}" in page.inner_text('#vista-radar button[data-estado="ENVIADO"]').replace("\n", " ")

        for (pid, postulacion), (canal, destino, asunto, cuerpo, enviado) in zip(posts, ENVIADAS):
            page.evaluate(f"window.Radar.abrirPost({pid})")
            page.wait_for_function(f"document.querySelector('#radarEmailEstado').textContent.includes('{_fecha_vista(enviado)}')")
            panel = page.inner_text("#radarEmailPanel")
            # Por email dice «Enviada»; marcada a mano en otro canal, «Postulada»
            assert ("Enviada" if canal == "email" else "Postulada") in panel
            if canal == "email":
                assert page.input_value("#radarEmailTo") == destino
                assert page.input_value("#radarEmailSubject") == asunto
            else:
                assert destino in page.inner_text("#radarDestinoCanal")
            assert page.input_value("#radarEmailBody") == cuerpo
            detalle = page.inner_text("#radarDetallePost")
            n = posts.index((pid, postulacion))
            assert f"Analista Funcional {n}" in detalle and f"Empresa {n}" in detalle

            for enlace, tipo in (("#radarVerPdf", "application/pdf"), ("#radarVerDocx", "wordprocessingml")):
                # A 375 px el mail está en otra pestaña: cuenta que el enlace no esté oculto por el post
                assert page.eval_on_selector(enlace, "e => !e.hidden"), f"{enlace} oculto en el post {pid} ({canal})"
                href = page.get_attribute(enlace, "href")
                assert href.endswith(f"/api/ofertas/{postulacion}/export/" + ("pdf" if "Pdf" in enlace else "docx"))
                r = page.request.get(base + href)
                assert r.status == 200 and tipo in r.headers["content-type"], (enlace, r.status)
        assert not errores.lista, errores.lista
    finally:
        contexto.close()


def test_postulacion_del_radar_sin_post_aparece_en_el_historial_del_radar(navegador, portal):
    base, _ = portal
    _sembrar_enviadas()
    huerfana = database.create_postulacion("Soporte de Aplicaciones", "Korva", "texto", status="READY",
                                           origen="linkedin", radar_post_ref="urn:li:activity:1")
    database.update_postulacion(huerfana, cv_markdown=SAMPLE_CV)
    database.create_postulacion("Oferta manual", "Velora Sistemas", "texto", status="READY")
    contexto, page, errores = _abrir(navegador, base, 1200)
    try:
        _vista(page, "manual")
        page.eval_on_selector("#manualHistorial", "d => { d.open = true; }")
        _vista(page, "radar")
        page.wait_for_selector("#manualHistorial:not([hidden])")
        # Abierto desde Manual no puede quedar abierto en el Radar: taparía la lista de posts
        assert page.eval_on_selector("#manualHistorial", "d => d.open") is False
        historial = page.inner_text("#manualHistorial summary")
        assert "Postulaciones sin post (1)" in historial
        page.eval_on_selector("#manualHistorial", "d => { d.open = true; }")
        lista = page.inner_text("#manualHistorialLista")
        assert "Soporte de Aplicaciones" in lista and "Oferta manual" not in lista
        hrefs = page.eval_on_selector_all("#manualHistorialLista a", "as => as.map(a => a.getAttribute('href'))")
        assert f"/api/ofertas/{huerfana}/export/pdf" in hrefs and f"/api/ofertas/{huerfana}/export/docx" in hrefs

        # La vista Manual sigue mostrando solo las manuales
        _vista(page, "manual")
        page.wait_for_function("document.querySelector('#manualHistorial summary').textContent.includes('Historial anterior (1)')")
        page.click("#manualHistorial summary")  # arranca cerrado (creador, 19/11): se abre al tocarlo
        assert "Oferta manual" in page.inner_text("#manualHistorialLista")
        assert not errores.lista, errores.lista
    finally:
        contexto.close()
