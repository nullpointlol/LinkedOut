"""Tests para verificar la eliminación completa y definitiva de ofertas (Descarte sin rastros)."""
import pytest
from fastapi.testclient import TestClient
from server import app
from core.database import create_postulacion, get_postulacion, list_postulaciones, delete_postulacion, DB_PATH, init_db
import sqlite3


@pytest.fixture(autouse=True)
def setup_isolated_db(tmp_path, monkeypatch):
    """Blindaje de tests: aísla la base de datos a un archivo temporal para proteger postulaciones reales."""
    test_db = tmp_path / "test_delete.db"
    monkeypatch.setattr("core.database.DB_PATH", test_db)
    init_db(test_db)
    yield
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass


@pytest.fixture
def client():
    return TestClient(app)



def test_delete_postulacion_db_directly():
    """delete_postulacion debe ejecutar DELETE físico en SQLite sin dejar rastros."""
    # Crear una postulación de prueba
    post_id = create_postulacion(
        titulo="Oferta Para Descartar",
        empresa="Empresa Temporal",
        contenido="Contenido para borrar",
    )
    assert post_id is not None
    assert get_postulacion(post_id) is not None

    # Ejecutar eliminación física
    deleted = delete_postulacion(post_id)
    assert deleted is True

    # Verificar ausencia total en base de datos
    assert get_postulacion(post_id) is None

    # Verificar que no exista en la tabla directamente con consulta SQL
    import core.database as db
    conn = sqlite3.connect(str(db.DB_PATH))
    cursor = conn.execute("SELECT COUNT(*) FROM postulaciones WHERE id = ?", (post_id,))
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 0, f"La postulación {post_id} sigue existiendo en SQLite"

    # Segunda llamada debe devolver False (ya no existe)
    deleted_again = delete_postulacion(post_id)
    assert deleted_again is False


def test_api_delete_oferta_success(client):
    """DELETE /api/ofertas/{id} debe eliminar la oferta y retornar 200 con status DELETED."""
    post_id = create_postulacion(
        titulo="Oferta Descarte API",
        empresa="Test S.A.",
        contenido="Texto de prueba",
    )

    response = client.delete(f"/api/ofertas/{post_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "DELETED"
    assert data["id"] == post_id

    # La consulta posterior por ID debe devolver 404
    get_res = client.get(f"/api/ofertas/{post_id}")
    assert get_res.status_code == 404

    # No debe figurar en el listado general
    list_res = client.get("/api/ofertas")
    assert list_res.status_code == 200
    ids_en_lista = [o["id"] for o in list_res.json()]
    assert post_id not in ids_en_lista


def test_api_delete_oferta_not_found(client):
    """DELETE /api/ofertas/{id} con un ID inexistente debe devolver HTTP 404."""
    response = client.delete("/api/ofertas/99999999")
    assert response.status_code == 404
    assert "no encontrada" in response.json()["detail"].lower()


def test_delete_isolation_preserves_other_offers(client):
    """Eliminar una oferta no debe afectar a las demás ofertas existentes."""
    id1 = create_postulacion(titulo="Oferta 1", empresa="E1", contenido="C1")
    id2 = create_postulacion(titulo="Oferta 2", empresa="E2", contenido="C2")

    try:
        # Eliminar solo la oferta 1
        res = client.delete(f"/api/ofertas/{id1}")
        assert res.status_code == 200

        # Oferta 1 eliminada
        assert get_postulacion(id1) is None

        # Oferta 2 intacta
        p2 = get_postulacion(id2)
        assert p2 is not None
        assert p2["titulo"] == "Oferta 2"
    finally:
        # Limpieza
        delete_postulacion(id2)
