"""Tests de regresión para cosecha y migración de imágenes en ofertas de LinkedIn."""
import sqlite3
from unittest.mock import AsyncMock

import pytest


def test_dom_legacy_y_actual():
    """Prueba que ambos DOM (legacy con data-urn y actual con tarjeta role='listitem') extraen imágenes de contenido."""
    from core.linkedin_parser import parse_posts_html

    html_legacy = """
    <div data-urn="urn:li:activity:7100000000000000001">
        <div class="update-components-actor">
            <span class="update-components-actor__title">María HR</span>
            <span class="update-components-actor__description">Recruiter at Acme</span>
        </div>
        <div class="update-components-text">Buscamos Desarrollador Python rrhh@acme.test</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/img_legacy_01.jpg" />
    </div>
    """
    posts_legacy = parse_posts_html(html_legacy)
    assert len(posts_legacy) == 1
    post_leg = posts_legacy[0]
    assert post_leg["urn"] == "urn:li:activity:7100000000000000001"
    assert post_leg["autor"] == "María HR"
    assert "Buscamos Desarrollador Python" in post_leg["texto"]
    assert post_leg["emails"] == ["rrhh@acme.test"]
    assert post_leg["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/img_legacy_01.jpg"
    ]

    html_actual = """
    <div role="listitem" componentkey="update-card-focus8200000000000000002FeedType_...">
        <p>Pedro Recruiter</p>
        <p>Talent Acquisition Lead</p>
        <div data-testid="expandable-text-box">Sumate a nuestro equipo talent@globex.test</div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/img_actual_02.jpg" />
    </div>
    """
    posts_actual = parse_posts_html(html_actual)
    assert len(posts_actual) == 1
    post_act = posts_actual[0]
    assert post_act["urn"] == "urn:li:card:8200000000000000002"
    assert post_act["autor"] == "Pedro Recruiter"
    assert "Sumate a nuestro equipo" in post_act["texto"]
    assert post_act["emails"] == ["talent@globex.test"]
    assert post_act["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/img_actual_02.jpg"
    ]


def test_solo_grafico_sin_caja():
    """Conserva posts con imagen válida aunque no tengan caja de texto (texto=''), y omite posts vacíos."""
    from core.linkedin_parser import parse_posts_html

    # DOM actual sin expandable-text-box pero con imagen válida
    html_actual = """
    <div role="listitem" componentkey="update-card-focus9300000000000000003FeedType_...">
        <p>Laura Ventas</p>
        <p>Directora Comercial</p>
        <a href="https://www.linkedin.com/in/laura-ventas/">Ver perfil de Laura Ventas</a>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/oferta_grafica_actual.png" />
    </div>
    """
    posts_actual = parse_posts_html(html_actual)
    assert len(posts_actual) == 1
    p_act = posts_actual[0]
    assert p_act["urn"] == "urn:li:card:9300000000000000003"
    assert p_act["autor"] == "Laura Ventas"
    assert p_act["cargo_autor"] == "Directora Comercial"
    assert p_act["texto"] == ""
    assert p_act["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/oferta_grafica_actual.png"
    ]

    # DOM legacy sin update-components-text pero con imagen válida
    html_legacy = """
    <div data-urn="urn:li:activity:9400000000000000004">
        <div class="update-components-actor">
            <span class="update-components-actor__title">Esteban Diseños</span>
            <span class="update-components-actor__description">Design Lead</span>
        </div>
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/oferta_grafica_legacy.png" />
    </div>
    """
    posts_legacy = parse_posts_html(html_legacy)
    assert len(posts_legacy) == 1
    p_leg = posts_legacy[0]
    assert p_leg["urn"] == "urn:li:activity:9400000000000000004"
    assert p_leg["autor"] == "Esteban Diseños"
    assert p_leg["texto"] == ""
    assert p_leg["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/oferta_grafica_legacy.png"
    ]

    # Post sin texto ni imagen válida: debe omitirse por completo
    html_vacio = """
    <div role="listitem" componentkey="update-card-focus9500000000000000005FeedType_...">
        <p>Solo autor sin texto ni imagen</p>
    </div>
    <div data-urn="urn:li:activity:9600000000000000006">
        <span class="update-components-actor__title">Sin texto ni imagen</span>
    </div>
    """
    assert parse_posts_html(html_vacio) == []


def test_excluye_comentarios_y_host_falso():
    """Excluye avatares, logos, hosts externos/falsos, credenciales, esquemas no HTTPS e imágenes en comentarios."""
    from core.linkedin_parser import parse_posts_html

    html = """
    <div role="listitem" componentkey="update-card-focus1111111111111111111FeedType_...">
        <p>Recruiter Host Check</p>
        <div data-testid="expandable-text-box">Texto de la publicación</div>

        <!-- 1. Imagen válida de feedshare -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/valida.jpg" />

        <!-- 2. Avatar de usuario (excluir) -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/profile-displayphoto-shrink_100/avatar.jpg" />

        <!-- 3. Avatar framed (excluir) -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/profile-framedphoto-shrink_100/framed.jpg" />

        <!-- 4. Logo de empresa (excluir) -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/company-logo_100_100/logo.png" />

        <!-- 5. Licdn pero sin feedshare en path (excluir) -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/article-cover_500/banner.jpg" />

        <!-- 6. Host falso / phishing con feedshare (excluir) -->
        <img src="https://evil-media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/evil.jpg" />
        <img src="https://media.licdn.com.attacker.com/feedshare-shrink_800/phish.jpg" />

        <!-- 7. Inseguro http (excluir) -->
        <img src="http://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/insecure.jpg" />

        <!-- 8. Con credenciales (excluir) -->
        <img src="https://user:pass@media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/creds.jpg" />

        <!-- 9. Fin del post: barra de acciones sociales y bloque de comentarios -->
        <button aria-label="Comentar en la publicación">Comentar</button>
        <div data-testid="commentlist">
            <div componentkey="urn:li:comment:123">
                <p>Comentario de tercero</p>
                <!-- Imagen dentro de comentarios: excluir aunque sea feedshare -->
                <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/en_comentario.jpg" />
            </div>
        </div>
    </div>
    """
    posts = parse_posts_html(html)
    assert len(posts) == 1
    post = posts[0]
    assert post["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/valida.jpg"
    ]


def test_limite_y_lazy():
    """Prioriza atributos válidos ignorando data:, deduplica y corta a un máximo de 4 imágenes."""
    from core.linkedin_parser import parse_posts_html

    html = """
    <div role="listitem" componentkey="update-card-focus2222222222222222222FeedType_...">
        <p>Galería de imágenes</p>
        <div data-testid="expandable-text-box">Oferta con múltiples fotos</div>

        <!-- img 1: src es data:, debe tomar data-src -->
        <img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="
             data-src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_1.jpg" />

        <!-- img 2: src es data:, debe tomar data-delayed-url antes que data-src -->
        <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
             data-delayed-url="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_2.jpg"
             data-src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_2_ignored.jpg" />

        <!-- Duplicado de foto_1: debe deduplicarse conservando orden -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_1.jpg" />

        <!-- img 3 -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_3.jpg" />

        <!-- img 4 -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_4.jpg" />

        <!-- img 5 y 6: exceden el límite de 4 imágenes por post -->
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_5.jpg" />
        <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_6.jpg" />
    </div>
    """
    posts = parse_posts_html(html)
    assert len(posts) == 1
    post = posts[0]
    assert len(post["imagenes"]) == 4
    assert post["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_1.jpg",
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_2.jpg",
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_3.jpg",
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_4.jpg",
    ]


def test_migracion_idempotente(tmp_path):
    """La migración parte de SQLite anterior sin las columnas, conserva fila/ID/estado y corre dos veces."""
    from core.radar_store import init_radar_tables, get_post, list_posts

    old_db = tmp_path / "old.db"
    conn = sqlite3.connect(old_db)
    # DDL anterior de radar_store omitiendo solo las columnas nuevas (imagenes, texto_imagen)
    conn.execute("""
        CREATE TABLE linkedin_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            urn TEXT UNIQUE NOT NULL,
            url TEXT,
            autor TEXT,
            cargo_autor TEXT,
            texto TEXT NOT NULL,
            fecha_rel TEXT,
            query TEXT,
            emails TEXT DEFAULT '[]',
            triage TEXT,
            estado TEXT DEFAULT 'NUEVO',
            postulacion_id INTEGER,
            email_to TEXT,
            email_subject TEXT,
            email_body TEXT,
            aprobado_at TIMESTAMP,
            sent_at TIMESTAMP,
            message_id TEXT,
            error TEXT,
            intentos INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fuente TEXT DEFAULT 'busqueda',
            links TEXT DEFAULT '[]',
            whatsapp TEXT DEFAULT '[]',
            canal TEXT DEFAULT '',
            destino_canal TEXT DEFAULT '',
            analisis TEXT,
            prioridad INTEGER DEFAULT 0,
            post_url TEXT DEFAULT '',
            progreso TEXT,
            gen_prioridad INTEGER DEFAULT 0
        );
    """)
    conn.execute(
        """
        INSERT INTO linkedin_posts (id, urn, autor, cargo_autor, texto, estado, fuente)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (42, "urn:li:activity:9999000000000000001", "Historica SA", "Tech Lead", "Oferta historica", "ANALIZADA", "busqueda"),
    )
    conn.commit()
    conn.close()

    # Ejecución 1 de la migración
    conn1 = sqlite3.connect(old_db)
    init_radar_tables(conn1)
    conn1.close()

    # Ejecución 2 de la migración (verificar idempotencia)
    conn2 = sqlite3.connect(old_db)
    init_radar_tables(conn2)
    conn2.close()

    # Verificar existencia de columnas agregadas
    conn_check = sqlite3.connect(old_db)
    cols = {row[1] for row in conn_check.execute("PRAGMA table_info(linkedin_posts)").fetchall()}
    conn_check.close()
    assert "imagenes" in cols
    assert "texto_imagen" in cols

    # get_post debe retornar fila original intacta con defaults [] y '' para NULLs históricos
    post = get_post(42, db_path=old_db)
    assert post is not None
    assert post["id"] == 42
    assert post["urn"] == "urn:li:activity:9999000000000000001"
    assert post["autor"] == "Historica SA"
    assert post["estado"] == "ANALIZADA"
    assert post["imagenes"] == []
    assert post["texto_imagen"] == ""

    # list_posts también entrega [] y ''
    todos = list_posts(db_path=old_db)
    assert len(todos) == 1
    assert todos[0]["id"] == 42
    assert todos[0]["imagenes"] == []
    assert todos[0]["texto_imagen"] == ""


def test_json_roundtrip(tmp_path):
    """insert_post_if_new y update_post aceptan imagenes y texto_imagen; get_post y list_posts deserializan JSON."""
    from core.radar_store import insert_post_if_new, get_post, update_post, list_posts

    db = tmp_path / "roundtrip.db"
    post_dict = {
        "urn": "urn:li:activity:7777000000000000001",
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:7777000000000000001/",
        "autor": "Tech recruiter",
        "cargo_autor": "Lead Recruiter",
        "texto": "Buscamos Backend Dev",
        "fecha_rel": "1 d",
        "emails": ["backend@test.test"],
        "imagenes": [
            "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_a.jpg",
            "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_b.jpg",
        ],
        "texto_imagen": "Requisitos: Python, FastAPI, Docker",
    }
    pid = insert_post_if_new(post_dict, query="python", db_path=db)
    assert isinstance(pid, int)

    # get_post deserializa JSON a list y string
    p = get_post(pid, db_path=db)
    assert p is not None
    assert p["imagenes"] == post_dict["imagenes"]
    assert p["texto_imagen"] == "Requisitos: Python, FastAPI, Docker"

    # update_post acepta imagenes y texto_imagen en whitelist
    nuevas_imagenes = [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/foto_c.jpg"
    ]
    actualizado = update_post(
        pid,
        db_path=db,
        imagenes=nuevas_imagenes,
        texto_imagen="Requisitos actualizados: Python, Kubernetes",
    )
    assert actualizado is True

    p2 = get_post(pid, db_path=db)
    assert p2["imagenes"] == nuevas_imagenes
    assert p2["texto_imagen"] == "Requisitos actualizados: Python, Kubernetes"

    # list_posts también deserializa correctamente
    lista = list_posts(db_path=db)
    assert len(lista) == 1
    assert lista[0]["imagenes"] == nuevas_imagenes
    assert lista[0]["texto_imagen"] == "Requisitos actualizados: Python, Kubernetes"


@pytest.mark.asyncio
async def test_scraper_y_manual(monkeypatch):
    """Scraper entrega imagenes en JSON-LD y permalink; ingesta manual propaga imagenes y acepta texto vacío con imagen."""
    from core.scraper import extract_job_from_html, scrape_job_url
    from server import _radar_ingestar_manual

    # 1. extract_job_from_html con JSON-LD incluye imagenes: list[str]
    html_json_ld = """
    <html>
    <head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Data Engineer",
            "hiringOrganization": {"name": "DataCorp"},
            "description": "Buscamos Data Engineer con experiencia sólida en SQL, Python y modelos de datos analíticos."
        }
        </script>
    </head>
    <body><h1>Data Engineer</h1></body>
    </html>
    """
    job_ld = extract_job_from_html(html_json_ld, url="https://example.com/job/10")
    assert job_ld["titulo"] == "Data Engineer"
    assert job_ld["empresa"] == "DataCorp"
    assert "imagenes" in job_ld
    assert job_ld["imagenes"] == []

    # 2. extract_job_from_html en permalink público con imagen gráfica de feedshare
    html_permalink = """
    <html>
    <head><title>Oferta en LinkedIn</title></head>
    <body>
        <div class="main-content">
            <h1>Oportunidad Laboral</h1>
            <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/profile-displayphoto-shrink_100/avatar.jpg" />
            <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/afiche_oferta.jpg" />
        </div>
        <div class="comments">
            <img src="https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/en_comentario.jpg" />
        </div>
    </body>
    </html>
    """
    job_grafico = extract_job_from_html(html_permalink, url="https://www.linkedin.com/feed/update/urn:li:activity:8888/")
    assert "imagenes" in job_grafico
    assert job_grafico["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/afiche_oferta.jpg"
    ]

    # 3. scrape_job_url ante error retorna imagenes: []
    monkeypatch.setattr(
        "core.scraper._fetch_url_sync",
        lambda url, timeout: (_ for _ in ()).throw(RuntimeError("Conexión rechazada")),
    )
    job_error = await scrape_job_url("https://inaccessible-url.org/job")
    assert "error" in job_error
    assert "imagenes" in job_error
    assert job_error["imagenes"] == []

    # 4. _radar_ingestar_manual propaga imagenes y acepta contenido vacío si hay imágenes válidas
    fake_job_ok = {
        "titulo": "Diseñador Gráfico",
        "empresa": "Estudio Creativo",
        "contenido": "",  # Sin texto pero con imagen
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:9999/",
        "imagenes": [
            "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/banner_empleo.png"
        ],
    }
    monkeypatch.setattr(
        "server.scrape_job_url",
        AsyncMock(return_value=fake_job_ok),
    )
    post_in = {
        "texto": "https://www.linkedin.com/feed/update/urn:li:activity:9999/",
        "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:9999/",
        "autor": "Manual",
        "cargo_autor": "",
    }
    resultado_manual = await _radar_ingestar_manual(post_in)
    assert resultado_manual["autor"] == "Estudio Creativo"
    assert resultado_manual["cargo_autor"] == "Diseñador Gráfico"
    assert resultado_manual["texto"] == ""
    assert resultado_manual["imagenes"] == [
        "https://media.licdn.com/dms/image/v2/D4D22AQ/feedshare-shrink_800/banner_empleo.png"
    ]

    # 5. _radar_ingestar_manual conserva error si la página es inaccesible o no tiene texto ni imágenes
    fake_job_vacio = {
        "titulo": "Oferta Importada por URL",
        "empresa": "Portal Web Externo",
        "contenido": "",
        "url": "https://bad-link.com",
        "error": "Timeout",
        "imagenes": [],
    }
    monkeypatch.setattr(
        "server.scrape_job_url",
        AsyncMock(return_value=fake_job_vacio),
    )
    with pytest.raises(RuntimeError, match="No se pudo leer la página de la oferta: pegá el texto"):
        await _radar_ingestar_manual({"texto": "https://bad-link.com", "post_url": "https://bad-link.com"})
