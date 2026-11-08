from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.database import SessionLocal
from app.main import app
from app.models import CustomerLedgerEntry


def setup_sale(client: TestClient) -> tuple[dict[str, str], str]:
    setup = client.post("/auth/setup", headers={"X-Bootstrap-Token": "test-bootstrap-token"}, json={"username": "financeadmin", "password": "a-safe-test-password"})
    assert setup.status_code == 201
    token = client.post("/auth/login", json={"username": "financeadmin", "password": "a-safe-test-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    product = client.post("/api/products", headers=headers, json={"name": "Finance Item", "sku": "FIN-1", "barcodes": [], "unit_of_measure": "each", "selling_price": "100.00", "tax_classification_status": "unknown"}).json()
    sale = client.post("/api/sales", headers=headers).json()
    assert client.post(f"/api/sales/{sale['id']}/lines/catalogue", headers=headers, json={"product_id": product["id"], "quantity": "10"}).status_code == 201
    assert client.post("/api/receipt-series", headers=headers, json={"series": "RET", "financial_year": "2026-27"}).status_code == 201
    issued = client.post(f"/api/sales/{sale['id']}/issue", headers={**headers, "Idempotency-Key": "issue-finance-0001"}, json={"series": "RET"})
    assert issued.status_code == 200
    assert issued.json()["receipt_number"] == "RET/2026-27/000001"
    return headers, sale["id"]


def test_cash_upi_split_and_idempotent_payments() -> None:
    client = TestClient(app)
    headers, sale_id = setup_sale(client)
    cash = client.post(f"/api/sales/{sale_id}/payments", headers={**headers, "Idempotency-Key": "cash-payment-001"}, json={"method": "cash", "amount": "300.00"})
    upi = client.post(f"/api/sales/{sale_id}/payments", headers={**headers, "Idempotency-Key": "upi-payment-001"}, json={"method": "upi", "amount": "500.00", "reference": "upi-ref"})
    repeat = client.post(f"/api/sales/{sale_id}/payments", headers={**headers, "Idempotency-Key": "cash-payment-001"}, json={"method": "cash", "amount": "300.00"})
    assert cash.status_code == 201 and upi.status_code == 201 and repeat.status_code == 201
    assert len(client.get(f"/api/sales/{sale_id}/payments", headers=headers).json()) == 2
    receipt = client.get("/api/receipts/RET/2026-27/000001", headers=headers)
    assert receipt.status_code == 200
    assert receipt.json()["total"] == "1000.00"
    assert receipt.json()["received_total"] == "800.00"
    assert receipt.json()["settled_total"] == "800.00"
    assert receipt.json()["balance_due"] == "200.00"
    assert receipt.json()["lines"][0]["line_total"] == "1000.00"
    assert len(receipt.json()["payments"]) == 2
    history = client.get("/api/payment-history", headers=headers)
    assert history.status_code == 200 and history.json()["total"] == 2
    overflow = client.post(f"/api/sales/{sale_id}/payments", headers={**headers, "Idempotency-Key": "bad-payment-001"}, json={"method": "cash", "amount": "300.01"})
    assert overflow.status_code == 409


def test_khata_credit_opening_balance_and_repayment() -> None:
    client = TestClient(app)
    headers, sale_id = setup_sale(client)
    customer = client.post("/api/customers", headers=headers, json={"name": "Synthetic Customer", "phone": "9000000000", "opening_balance": "50.00"})
    assert customer.status_code == 201
    customer_id = customer.json()["id"]
    khata = client.post(f"/api/sales/{sale_id}/payments", headers={**headers, "Idempotency-Key": "khata-payment-001"}, json={"method": "khata", "amount": "200.00", "customer_id": customer_id})
    assert khata.status_code == 201
    receipt = client.get("/api/receipts/RET/2026-27/000001", headers=headers).json()
    assert receipt["received_total"] == "0.00"
    assert receipt["khata_total"] == "200.00"
    assert receipt["settled_total"] == "200.00"
    assert receipt["balance_due"] == "800.00"
    statement = client.get(f"/api/customers/{customer_id}/statement", headers=headers).json()
    assert statement["outstanding_balance"] == "250.00"
    repayment = client.post(f"/api/customers/{customer_id}/repayments", headers={**headers, "Idempotency-Key": "repayment-0001"}, json={"amount": "100.00", "reference": "cash"})
    assert repayment.status_code == 201
    assert client.get(f"/api/customers/{customer_id}/statement", headers=headers).json()["outstanding_balance"] == "150.00"
    duplicate = client.post(f"/api/customers/{customer_id}/repayments", headers={**headers, "Idempotency-Key": "repayment-0001"}, json={"amount": "100.00"})
    assert duplicate.status_code == 201


def test_finance_operational_reads_require_authentication() -> None:
    client = TestClient(app)
    assert client.get("/operations").status_code == 200
    assert client.get("/api/customers").status_code == 401
    assert client.get("/api/payment-history").status_code == 401
    assert client.get("/api/receipts/RET/2026-27/000001").status_code == 401


def test_admin_adjustments_are_idempotent_and_cashier_is_forbidden() -> None:
    client = TestClient(app)
    headers, _ = setup_sale(client)
    customer_id = client.post("/api/customers", headers=headers, json={"name": "Adjustment Customer"}).json()["id"]
    debit = client.post(f"/api/customers/{customer_id}/adjustments", headers={**headers, "Idempotency-Key": "adjustment-debit-001"}, json={"amount": "120.00", "direction": "debit", "reason": "Correct opening balance entered too low"})
    credit = client.post(f"/api/customers/{customer_id}/adjustments", headers={**headers, "Idempotency-Key": "adjustment-credit-01"}, json={"amount": "20.00", "direction": "credit", "reason": "Approved price correction for duplicate charge"})
    assert debit.status_code == 201 and credit.status_code == 201
    assert client.get(f"/api/customers/{customer_id}/statement", headers=headers).json()["outstanding_balance"] == "100.00"
    repeated = client.post(f"/api/customers/{customer_id}/adjustments", headers={**headers, "Idempotency-Key": "adjustment-debit-001"}, json={"amount": "120.00", "direction": "debit", "reason": "Correct opening balance entered too low"})
    assert repeated.status_code == 201
    invalid = client.post(f"/api/customers/{customer_id}/adjustments", headers={**headers, "Idempotency-Key": "adjustment-bad-0001"}, json={"amount": "1.00", "direction": "debit", "reason": "short"})
    assert invalid.status_code == 422
    cashier = client.post("/admin/users", headers=headers, json={"username": "ledgercashier", "password": "another-safe-password", "role": "cashier"})
    assert cashier.status_code == 201
    cashier_token = client.post("/auth/login", json={"username": "ledgercashier", "password": "another-safe-password"}).json()["access_token"]
    forbidden = client.post(f"/api/customers/{customer_id}/adjustments", headers={"Authorization": f"Bearer {cashier_token}", "Idempotency-Key": "adjustment-cashier-1"}, json={"amount": "1.00", "direction": "debit", "reason": "Unauthorized correction request"})
    assert forbidden.status_code == 403
    forbidden_reversal = client.post(f"/api/customers/{customer_id}/ledger/{debit.json()['id']}/reversals", headers={"Authorization": f"Bearer {cashier_token}", "Idempotency-Key": "reversal-cashier-1"}, json={"reason": "Unauthorized reversal attempt"})
    assert forbidden_reversal.status_code == 403


def test_reversal_is_compensating_and_only_once() -> None:
    client = TestClient(app)
    headers, sale_id = setup_sale(client)
    customer_id = client.post("/api/customers", headers=headers, json={"name": "Reversal Customer"}).json()["id"]
    credit_sale = client.post(f"/api/sales/{sale_id}/payments", headers={**headers, "Idempotency-Key": "khata-reversal-001"}, json={"method": "khata", "amount": "200.00", "customer_id": customer_id})
    assert credit_sale.status_code == 201
    original = client.get(f"/api/customers/{customer_id}/statement", headers=headers).json()["entries"][0]
    reversal = client.post(f"/api/customers/{customer_id}/ledger/{original['id']}/reversals", headers={**headers, "Idempotency-Key": "reversal-credit-sale-1"}, json={"reason": "Approved cancellation of the credit sale"})
    assert reversal.status_code == 201
    assert reversal.json()["direction"] == "credit"
    assert reversal.json()["reversal_of_id"] == original["id"]
    assert client.get(f"/api/customers/{customer_id}/statement", headers=headers).json()["outstanding_balance"] == "0.00"
    retry = client.post(f"/api/customers/{customer_id}/ledger/{original['id']}/reversals", headers={**headers, "Idempotency-Key": "reversal-credit-sale-1"}, json={"reason": "Approved cancellation of the credit sale"})
    duplicate = client.post(f"/api/customers/{customer_id}/ledger/{original['id']}/reversals", headers={**headers, "Idempotency-Key": "reversal-credit-sale-2"}, json={"reason": "Second attempt should be rejected"})
    assert retry.status_code == 201 and duplicate.status_code == 409


def test_reversal_of_repayment_restores_customer_balance() -> None:
    client = TestClient(app)
    headers, _ = setup_sale(client)
    customer_id = client.post("/api/customers", headers=headers, json={"name": "Repayment Reversal Customer", "opening_balance": "90.00"}).json()["id"]
    repayment = client.post(f"/api/customers/{customer_id}/repayments", headers={**headers, "Idempotency-Key": "repayment-reversal-01"}, json={"amount": "40.00", "reference": "Synthetic cash repayment"})
    assert repayment.status_code == 201
    reverse = client.post(f"/api/customers/{customer_id}/ledger/{repayment.json()['id']}/reversals", headers={**headers, "Idempotency-Key": "reversal-repayment-01"}, json={"reason": "Approved correction of wrongly posted repayment"})
    assert reverse.status_code == 201 and reverse.json()["direction"] == "debit"
    assert client.get(f"/api/customers/{customer_id}/statement", headers=headers).json()["outstanding_balance"] == "90.00"


def test_date_filtered_paginated_statement_keeps_running_balance() -> None:
    client = TestClient(app)
    headers, _ = setup_sale(client)
    customer_id = client.post("/api/customers", headers=headers, json={"name": "Statement Customer"}).json()["id"]
    entry_ids = []
    for key, direction, amount, reason in [("statement-date-001", "debit", "100.00", "Opening correction reason"), ("statement-date-002", "credit", "25.00", "Repayment correction reason"), ("statement-date-003", "debit", "10.00", "Late approved adjustment")]:
        response = client.post(f"/api/customers/{customer_id}/adjustments", headers={**headers, "Idempotency-Key": key}, json={"amount": amount, "direction": direction, "reason": reason})
        assert response.status_code == 201
        entry_ids.append(response.json()["id"])
    with SessionLocal() as session:
        for entry_id, timestamp in zip(entry_ids, [datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc), datetime(2026, 1, 2, 18, 0, tzinfo=timezone.utc), datetime(2026, 1, 3, 18, 0, tzinfo=timezone.utc)]):
            session.get(CustomerLedgerEntry, entry_id).created_at = timestamp
        session.commit()
    statement = client.get(f"/api/customers/{customer_id}/statement?start_date=2026-01-02&end_date=2026-01-03&limit=1&offset=1", headers=headers)
    assert statement.status_code == 200
    body = statement.json()
    assert body["opening_balance"] == "100.00"
    assert body["closing_balance"] == "85.00"
    assert body["total"] == 2
    assert len(body["entries"]) == 1 and body["entries"][0]["running_balance"] == "85.00"
