from fastapi.testclient import TestClient

from app.main import app


def admin_headers(client: TestClient) -> dict[str, str]:
    setup = client.post("/auth/setup", headers={"X-Bootstrap-Token": "test-bootstrap-token"}, json={"username": "checkoutadmin", "password": "a-safe-test-password"})
    assert setup.status_code == 201
    token = client.post("/auth/login", json={"username": "checkoutadmin", "password": "a-safe-test-password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_product(client: TestClient, headers: dict[str, str], **changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Green Tea", "sku": "TEA-001", "barcodes": ["000123"], "unit_of_measure": "box",
        "category": "beverages", "mrp": "120.00", "selling_price": "100.00", "tax_classification_status": "unknown",
    }
    payload.update(changes)
    response = client.post("/api/products", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_checkout_search_prioritizes_barcode_and_sku_and_returns_multiple_choices() -> None:
    client = TestClient(app)
    headers = admin_headers(client)
    scanned = create_product(client, headers)
    sku_only = create_product(client, headers, name="Green Tea Large", sku="TEA-SKU", barcodes=[], category="beverages")
    create_product(client, headers, name="Green Tea Lemon", sku="TEA-LEMON", barcodes=["009"], category="beverages")

    exact_barcode = client.get("/api/products/search?query=000123", headers=headers).json()["items"]
    exact_sku = client.get("/api/products/search?query=TEA-SKU", headers=headers).json()["items"]
    partial_name = client.get("/api/products/search?query=gReEn%20tEa&category=beverages", headers=headers).json()["items"]

    assert exact_barcode[0]["id"] == scanned["id"]
    assert exact_barcode[0]["match_type"] == "exact_barcode"
    assert exact_sku[0]["id"] == sku_only["id"]
    assert exact_sku[0]["match_type"] == "exact_sku"
    assert len(partial_name) == 3
    assert all({"name", "sku", "unit_of_measure", "mrp", "selling_price"} <= set(item) for item in partial_name)


def test_product_without_barcode_is_searchable_by_partial_sku_and_billable() -> None:
    client = TestClient(app)
    headers = admin_headers(client)
    product = create_product(client, headers, name="Loose Rice", sku="RICE-LOOSE", barcodes=[], unit_of_measure="kg")
    found = client.get("/api/products/search?query=LOO", headers=headers).json()["items"]
    assert found[0]["id"] == product["id"]
    assert found[0]["match_type"] == "partial_sku"

    sale = client.post("/api/sales", headers=headers).json()
    added = client.post(f"/api/sales/{sale['id']}/lines/catalogue", headers=headers, json={"product_id": product["id"], "quantity": "1.250"})
    assert added.status_code == 201
    assert added.json()["lines"][0]["product_id"] == product["id"]


def test_temporary_line_stays_out_of_catalogue_and_issued_sale_preserves_snapshots() -> None:
    client = TestClient(app)
    headers = admin_headers(client)
    product = create_product(client, headers, name="Snapshot Soap", sku="SOAP-1", barcodes=["SOAP001"], selling_price="40.00", mrp="45.00")
    before = client.get("/api/products", headers=headers).json()["total"]
    sale = client.post("/api/sales", headers=headers).json()
    catalogue = client.post(f"/api/sales/{sale['id']}/lines/catalogue", headers=headers, json={"product_id": product["id"], "quantity": "2"})
    assert catalogue.status_code == 201
    temporary = client.post(
        f"/api/sales/{sale['id']}/lines/temporary", headers=headers,
        json={"description": "Counter special", "unit": "each", "quantity": "1", "price": "15.00", "tax_classification_status": "unknown"},
    )
    assert temporary.status_code == 201
    series = client.post("/api/receipt-series", headers=headers, json={"series": "TEST", "financial_year": "2026-27"})
    assert series.status_code == 201
    issued = client.post(f"/api/sales/{sale['id']}/issue", headers={**headers, "Idempotency-Key": "issue-checkout-0001"}, json={"series": "TEST"})
    assert issued.status_code == 200
    assert issued.json()["status"] == "issued"
    assert issued.json()["lines"][1]["is_temporary"] is True
    assert issued.json()["lines"][1]["product_id"] is None
    assert issued.json()["lines"][1]["tax_classification_status"] == "unknown"
    assert client.get("/api/products", headers=headers).json()["total"] == before

    changed = client.patch(f"/api/products/{product['id']}", headers=headers, json={"name": "Changed Soap", "selling_price": "99.00"})
    assert changed.status_code == 200
    stored = client.get(f"/api/sales/{sale['id']}", headers=headers).json()
    assert stored["lines"][0]["description"] == "Snapshot Soap"
    assert stored["lines"][0]["unit_price"] == "40.00"
    assert client.post(f"/api/sales/{sale['id']}/lines/temporary", headers=headers, json={"description": "Later", "unit": "each", "quantity": "1", "price": "1", "tax_classification_status": "unknown"}).status_code == 409
