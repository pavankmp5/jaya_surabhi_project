from fastapi.testclient import TestClient

from app.main import app


def create_admin(client: TestClient) -> str:
    response = client.post(
        "/auth/setup",
        headers={"X-Bootstrap-Token": "test-bootstrap-token"},
        json={"username": "owner", "password": "a-safe-test-password"},
    )
    assert response.status_code == 201
    return client.post(
        "/auth/login",
        json={"username": "owner", "password": "a-safe-test-password"},
    ).json()["access_token"]


def test_protected_endpoint_requires_authentication() -> None:
    response = TestClient(app).get("/auth/me")

    assert response.status_code == 401


def test_bootstrap_requires_the_configured_token() -> None:
    response = TestClient(app).post(
        "/auth/setup",
        json={"username": "owner", "password": "a-safe-test-password"},
    )

    assert response.status_code == 403


def test_admin_can_create_user_and_cashier_cannot_manage_users() -> None:
    client = TestClient(app)
    admin_token = create_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    create_cashier = client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": "cashier", "password": "another-safe-password", "role": "cashier"},
    )
    assert create_cashier.status_code == 201

    cashier_token = client.post(
        "/auth/login",
        json={"username": "cashier", "password": "another-safe-password"},
    ).json()["access_token"]
    forbidden = client.post(
        "/admin/users",
        headers={"Authorization": f"Bearer {cashier_token}"},
        json={"username": "viewer", "password": "third-safe-password", "role": "viewer"},
    )

    assert forbidden.status_code == 403


def test_login_rejects_invalid_password() -> None:
    client = TestClient(app)
    create_admin(client)

    response = client.post("/auth/login", json={"username": "owner", "password": "wrong-password-value"})

    assert response.status_code == 401
