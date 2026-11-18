import pytest
"""Republicaciones del mismo autor y letras «negrita» de Unicode (tickets del dev 1112, 15/11)."""
from core.idioma import EN, idioma_cv_de_oferta, idioma_de_texto
from core.radar_store import get_post, insert_post_if_new, republicacion_de, update_post

OFERTA = ("WE'RE HIRING: TECHNICAL SUPPORT SPECIALIST. 100% Remote, LATAM. Requirements: 2 years in help desk, "
          "ticketing tools, advanced English. Send your CV to jobs@example.com " * 6)


def _guardar(n, texto, puesto=None, autor="Rita Prueba"):
    pid = insert_post_if_new({"urn": f"urn:li:activity:9{n}", "autor": autor, "texto": texto}, query="q")
    if puesto:
        update_post(pid, triage={"puesto": puesto})
    return get_post(pid)


def test_la_misma_oferta_con_otro_saludo_es_una_republicacion():
    primera = _guardar(1, "Buen sábado para todos! " + OFERTA, "Technical Support Specialist")
    segunda = _guardar(2, "Feliz lunes!! Seguimos con esta posición: " + OFERTA)
    assert republicacion_de(segunda, "Technical Support SPECIALIST") == primera["id"]


def test_otro_puesto_con_la_misma_plantilla_no_es_republicacion():
    _guardar(3, OFERTA, "Technical Support Specialist")
    otra = _guardar(4, OFERTA.replace("TECHNICAL SUPPORT SPECIALIST", "QA ANALYST"))
    assert republicacion_de(otra, "QA Analyst") is None


def test_otro_autor_no_es_republicacion():
    _guardar(5, OFERTA, "Technical Support Specialist")
    otra = _guardar(6, OFERTA, autor="Otra Consultora")
    assert republicacion_de(otra, "Technical Support Specialist") is None


def test_un_texto_distinto_del_mismo_autor_y_puesto_no_es_republicacion():
    _guardar(7, OFERTA, "Technical Support Specialist")
    otra = _guardar(8, "Technical Support Specialist para un banco, presencial en CABA, turno noche, "
                       "con experiencia en redes y Active Directory. Enviar CV a rrhh@banco.example")
    assert republicacion_de(otra, "Technical Support Specialist") is None


NEGRITA = ("𝗘𝘅𝗽𝗹𝗼𝗿𝗲 𝗖𝗮𝗿𝗲𝗲𝗿 𝗢𝗽𝗽𝗼𝗿𝘁𝘂𝗻𝗶𝘁𝗶𝗲𝘀 𝗶𝗻 𝗗𝗮𝘁𝗮! 𝐖𝐞 𝐚𝐫𝐞 𝐢𝐧𝐯𝐢𝐭𝐢𝐧𝐠 𝐚𝐩𝐩𝐥𝐢𝐜𝐚𝐭𝐢𝐨𝐧𝐬 𝐟𝐨𝐫 𝐭𝐡𝐞 "
           "𝐫𝐨𝐥𝐞 𝐨𝐟 𝐝𝐚𝐭𝐚 𝐚𝐧𝐚𝐥𝐲𝐬𝐭 𝐰𝐢𝐭𝐡 𝐲𝐞𝐚𝐫𝐬 𝐨𝐟 𝐞𝐱𝐩𝐞𝐫𝐢𝐞𝐧𝐜𝐞 𝐢𝐧 𝐭𝐡𝐞 𝐭𝐞𝐚𝐦 𝐚𝐧𝐝 𝐲𝐨𝐮 𝐰𝐢𝐥𝐥 𝐰𝐨𝐫𝐤 𝐰𝐢𝐭𝐡 𝐨𝐮𝐫 𝐬𝐤𝐢𝐥𝐥𝐬.")


def test_una_oferta_en_negrita_de_unicode_se_lee_en_ingles():
    assert idioma_cv_de_oferta(NEGRITA) == EN and idioma_de_texto(NEGRITA) == EN


def test_regenerar_recalcula_el_idioma_con_el_texto_de_la_oferta():
    # oferta 8201 (ticket 186): el análisis guardó «es» antes del arreglo de la negrita y «Regenerar» lo reusaba
    import server
    assert server._idioma_del_cv({"contenido_oferta": NEGRITA}, {"idioma_cv": "es"}) == EN
    # sin texto de la oferta queda el idioma guardado
    assert server._idioma_del_cv({"contenido_oferta": ""}, {"idioma_cv": EN}) == EN
    assert server._idioma_del_cv({}, {}) == "es"


CORTA_EN = ("🚀 We’re hiring: Purview Solutions Architect\nJoin Kopius on a consulting engagement focused on Microsoft "
            "Purview, Information Protection, Data Loss Prevention (DLP), Data Governance, and Compliance.\n"
            "🌎 Remote | LATAM or USA\n👉 Send your resume to careers@kopiustech.test\n#job #Latam #Purview #opportunity")


@pytest.mark.parametrize("texto, esperado", [
    (CORTA_EN, EN),  # oferta 1792: corta, sin ninguna palabra castellana
    ("Buscamos Data Analyst para el equipo de Business Intelligence. Remote first. Send CV.", "es"),
    ("Data Analyst | Power BI | SQL | Remote", "es"),  # sin palabras frecuentes de ningún idioma
])
def test_una_oferta_corta_en_ingles_se_lee_en_ingles(texto, esperado):
    assert idioma_cv_de_oferta(texto) == esperado
