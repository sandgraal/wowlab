from fastapi.testclient import TestClient

from bronze_api import __version__


def test_health_reports_ok_and_version(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
