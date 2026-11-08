from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import ProductBarcode


def create_admin(client: TestClient) -> dict[str, str]:
    setup = client.post(
        "/auth/setup",
        headers={"X-Bootstrap-Token": "test-bootstrap-token"},
        json={"username": "catalogadmin", "password": "a-safe-test-password"},
    )
    assert setup.status_code == 201
    token = client.post(
        "/auth/login",
        json={"username": "catalogadmin", "password": "a-safe-test-password"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def product_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Rice 5 kg",
        "description": "Synthetic test product",
        "sku": "RICE-5KG",
        "barcodes": ["0001234567890123"],
        "unit_of_measure": "pack",
        "pack_size": "5 kg",
        "selling_price": "425.50",
        "purchase_price": "380.00",
        "tax_classification_status": "unknown",
    }
    payload.update(changes)
    return payload


def create_product(client: TestClient, headers: dict[str, str], **changes: object) -> dict[str, object]:
    response = client.post("/api/products", headers=headers, json=product_payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def test_product_creation_retrieval_and_leading_zero_barcode_round_trip() -> None:
    client = TestClient(app)
    headers = create_admin(client)

    created = create_product(client, headers)
    retrieved = client.get(f"/api/products/{created['id']}", headers=headers)

    assert retrieved.status_code == 200
    assert retrieved.json()["barcodes"] == ["0001234567890123"]
    assert retrieved.json()["selling_price"] == "425.50"
    with SessionLocal() as session:
        assert session.scalar(select(ProductBarcode.value)) == "0001234567890123"


def test_product_routes_require_authentication_and_admin_writes() -> None:
    client = TestClient(app)
    assert client.get("/api/products").status_code == 401

    admin_headers = create_admin(client)
    user_response = client.post(
        "/admin/users",
        headers=admin_headers,
        json={"username": "cashieruser", "password": "another-safe-password", "role": "cashier"},
    )
    assert user_response.status_code == 201
    cashier_token = client.post(
        "/auth/login",
        json={"username": "cashieruser", "password": "another-safe-password"},
    ).json()["access_token"]
    cashier_headers = {"Authorization": f"Bearer {cashier_token}"}

    assert client.post("/api/products", headers=cashier_headers, json=product_payload()).status_code == 403
    assert client.get("/api/products", headers=cashier_headers).status_code == 200


def test_duplicate_barcode_and_sku_return_conflict() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    create_product(client, headers)

    duplicate_barcode = client.post(
        "/api/products",
        headers=headers,
        json=product_payload(name="Rice 1 kg", sku="RICE-1KG"),
    )
    duplicate_sku = client.post(
        "/api/products",
        headers=headers,
        json=product_payload(name="Rice 10 kg", barcodes=["another-code"], sku="RICE-5KG"),
    )

    assert duplicate_barcode.status_code == 409
    assert duplicate_sku.status_code == 409


def test_invalid_prices_and_barcodes_are_rejected_without_reformatting() -> None:
    client = TestClient(app)
    headers = create_admin(client)

    assert client.post(
        "/api/products", headers=headers, json=product_payload(selling_price="0")
    ).status_code == 422
    assert client.post(
        "/api/products", headers=headers, json=product_payload(selling_price="not-a-price")
    ).status_code == 422
    assert client.post(
        "/api/products", headers=headers, json=product_payload(barcodes=[" 00123"])
    ).status_code == 422
    assert client.post(
        "/api/products", headers=headers, json=product_payload(barcodes=[""])
    ).status_code == 422
    long_code = "x" * 128
    assert create_product(client, headers, sku="LONG-CODE", barcodes=[long_code])["barcodes"] == [long_code]


def test_update_adds_barcode_and_deactivation_preserves_product() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    created = create_product(client, headers)

    updated = client.patch(
        f"/api/products/{created['id']}",
        headers=headers,
        json={"selling_price": "399.99", "barcodes_to_add": ["099999"]},
    )
    deactivated = client.post(f"/api/products/{created['id']}/deactivate", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["barcodes"] == ["0001234567890123", "099999"]
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert client.get(f"/api/products/{created['id']}", headers=headers).status_code == 200


def test_list_search_pagination_and_active_filter() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    first = create_product(client, headers, name="Alpha Tea", sku="ALPHA", barcodes=["001"])
    create_product(client, headers, name="Beta Soap", sku="BETA", barcodes=["002"])
    client.post(f"/api/products/{first['id']}/deactivate", headers=headers)

    search = client.get("/api/products?search=001", headers=headers)
    active = client.get("/api/products?active=true&limit=1", headers=headers)
    inactive = client.get("/api/products?active=false", headers=headers)

    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["name"] == "Alpha Tea"
    assert active.json()["total"] == 1
    assert active.json()["limit"] == 1
    assert inactive.json()["items"][0]["id"] == first["id"]
