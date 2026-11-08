from datetime import timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.main import app
from app.models import ProductImportPreviewRecord
from app.routers.product_imports import _utcnow


def create_admin(client: TestClient) -> dict[str, str]:
    response = client.post("/auth/setup", headers={"X-Bootstrap-Token": "test-bootstrap-token"}, json={"username": "importadmin", "password": "a-safe-test-password"})
    assert response.status_code == 201
    token = client.post("/auth/login", json={"username": "importadmin", "password": "a-safe-test-password"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def csv_file(content: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("catalogue.csv", content, "text/csv")}


def valid_csv(rows: str = "Test Rice,RICE-1,000123456789,pack,125.50,unknown\n") -> bytes:
    return ("name,sku,barcodes,unit_of_measure,selling_price,tax_classification_status\n" + rows).encode("utf-8")


def preview(client: TestClient, headers: dict[str, str], content: bytes) -> dict[str, object]:
    response = client.post("/api/product-imports/preview", headers=headers, files=csv_file(content))
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client: TestClient, headers: dict[str, str], token: str):
    return client.post("/api/product-imports/confirm", headers=headers, json={"confirmation_token": token})


def test_csv_preview_displays_proposals_and_confirm_is_atomic_and_audited() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    result = preview(client, headers, valid_csv())

    assert result["valid_rows"] == 1
    assert result["rejected_rows"] == 0
    assert result["proposed_additions"][0]["barcodes"] == ["000123456789"]
    assert client.get("/api/products", headers=headers).json()["total"] == 0

    confirmed = confirm(client, headers, result["confirmation_token"])
    assert confirmed.status_code == 201
    assert confirmed.json() == {"created_count": 1, "rejected_count": 0, "conflicting_count": 0}
    assert client.get("/api/products", headers=headers).json()["items"][0]["barcodes"] == ["000123456789"]
    preview_id = result["confirmation_token"].split(".", 1)[0]
    with SessionLocal() as session:
        record = session.get(ProductImportPreviewRecord, preview_id)
        assert record is not None
        assert record.status == "confirmed"
        assert record.confirmed_by_user_id is not None
        assert record.confirmed_at is not None
        assert record.proposed_rows == []


def test_xlsx_preview_accepts_text_barcode_and_rejects_numeric_barcode() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "sku", "barcodes", "unit_of_measure", "selling_price"])
    sheet.append(["Excel Rice", "SHEET-1", "00099", "pack", "20.50"])
    stream = BytesIO()
    workbook.save(stream)
    success = client.post("/api/product-imports/preview", headers=headers, files={"file": ("catalogue.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert success.status_code == 200
    assert success.json()["proposed_additions"][0]["barcodes"] == ["00099"]

    sheet["C2"] = 99
    stream = BytesIO()
    workbook.save(stream)
    rejected = client.post("/api/product-imports/preview", headers=headers, files={"file": ("numeric.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert rejected.status_code == 200
    assert rejected.json()["valid_rows"] == 0
    assert rejected.json()["errors"][0]["field"] == "barcodes"


def test_preview_reports_headers_validation_duplicates_and_existing_conflicts() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    malformed = client.post("/api/product-imports/preview", headers=headers, files=csv_file(b"name,sku\nOnly,ONE\n"))
    assert malformed.status_code == 422

    duplicate_rows = valid_csv("One,DUP,001,pack,10,unknown\nTwo,DUP,001,pack,20,unknown\n")
    duplicate = preview(client, headers, duplicate_rows)
    assert duplicate["valid_rows"] == 1
    assert duplicate["rejected_rows"] == 1
    assert duplicate["conflicting_rows"] == 1
    assert duplicate["confirmation_token"] is None

    invalid_rows = valid_csv("Bad Tax,BAD-TAX,123,pack,nope,imagined-rate\n")
    invalid = preview(client, headers, invalid_rows)
    assert invalid["valid_rows"] == 0
    assert invalid["errors"][0]["kind"] == "validation"

    created = client.post("/api/products", headers=headers, json={"name": "Existing", "sku": "EXISTING", "barcodes": ["000123456789"], "unit_of_measure": "each", "selling_price": "10"})
    assert created.status_code == 201
    existing = preview(client, headers, valid_csv("Again,NEW-SKU,000123456789,pack,10,unknown\n"))
    assert existing["valid_rows"] == 0
    assert existing["conflicting_rows"] == 1


def test_confirmation_rejects_tampering_expiry_repeat_and_other_admin() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    result = preview(client, headers, valid_csv())
    token = result["confirmation_token"]
    assert confirm(client, headers, token + "changed").status_code == 403

    created = client.post("/admin/users", headers=headers, json={"username": "secondadmin", "password": "another-safe-password", "role": "admin"})
    assert created.status_code == 201
    second_token = client.post("/auth/login", json={"username": "secondadmin", "password": "another-safe-password"}).json()["access_token"]
    other_admin = {"Authorization": f"Bearer {second_token}"}
    assert confirm(client, other_admin, token).status_code == 403

    confirmed = confirm(client, headers, token)
    assert confirmed.status_code == 201
    assert confirm(client, headers, token).status_code == 409

    expiring = preview(client, headers, valid_csv("Expiry,EXP-1,889,each,5,unknown\n"))
    expiring_id = expiring["confirmation_token"].split(".", 1)[0]
    with SessionLocal() as session:
        record = session.get(ProductImportPreviewRecord, expiring_id)
        record.expires_at = _utcnow() - timedelta(seconds=1)
        session.commit()
    assert confirm(client, headers, expiring["confirmation_token"]).status_code == 409
    with SessionLocal() as session:
        assert session.get(ProductImportPreviewRecord, expiring_id).proposed_rows == []


def test_concurrent_catalogue_change_and_database_failure_create_no_partial_products() -> None:
    client = TestClient(app)
    headers = create_admin(client)
    result = preview(client, headers, valid_csv())
    concurrent = client.post("/api/products", headers=headers, json={"name": "Concurrent", "sku": "CONCURRENT", "barcodes": ["000123456789"], "unit_of_measure": "each", "selling_price": "10"})
    assert concurrent.status_code == 201
    assert confirm(client, headers, result["confirmation_token"]).status_code == 409
    assert client.get("/api/products", headers=headers).json()["total"] == 1

    failing = preview(client, headers, valid_csv("First,ROLL-1,100,each,5,unknown\nForce Failure,ROLL-2,101,each,5,unknown\n"))
    with engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER reject_import BEFORE INSERT ON products WHEN NEW.name = 'Force Failure' BEGIN SELECT RAISE(ABORT, 'forced failure'); END;"))
    try:
        assert confirm(client, headers, failing["confirmation_token"]).status_code == 409
        products = client.get("/api/products", headers=headers).json()["items"]
        assert all(product["sku"] not in {"ROLL-1", "ROLL-2"} for product in products)
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER IF EXISTS reject_import"))


def test_unauthorized_and_cashier_requests_are_rejected() -> None:
    client = TestClient(app)
    content = valid_csv()
    assert client.post("/api/product-imports/preview", files=csv_file(content)).status_code == 401
    admin = create_admin(client)
    created = client.post("/admin/users", headers=admin, json={"username": "cashierimport", "password": "another-safe-password", "role": "cashier"})
    assert created.status_code == 201
    token = client.post("/auth/login", json={"username": "cashierimport", "password": "another-safe-password"}).json()["access_token"]
    assert client.post("/api/product-imports/preview", headers={"Authorization": f"Bearer {token}"}, files=csv_file(content)).status_code == 403
