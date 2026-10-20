from fastapi.testclient import TestClient

from velora.app import create_app
from velora.config import Settings


def test_healthz_returns_ok() -> None:
    app = create_app(Settings(env="test", log_level="INFO"))
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
