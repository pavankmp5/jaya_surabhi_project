# Jaya Surabhi Super Market POS

This repository is an early POS service with authenticated product catalogue, checkout, receipt numbering, payment recording, khata statements, controlled ledger adjustments/reversals, and a small finance operations page. It does not yet manage stock movements or purchase entries. Keep BUSY as the system of record until the POS is tested and reconciled against copied exports.

## Development setup (Windows PowerShell)

Use Python 3.12 or newer, below 3.15. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
# Edit .env with unique, high-entropy values before using authentication.
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "Env:$($matches[1])" -Value $matches[2] }
}
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check `http://127.0.0.1:8000/` or `http://127.0.0.1:8000/health`; both return HTTP 200. Run the automated checks with `python -m pytest`.

Before creating the first administrator, set `POS_AUTH_SECRET` to at least 32 random characters and set a separate `POS_BOOTSTRAP_TOKEN`. After migration, call `POST /auth/setup` once with the `X-Bootstrap-Token` header and a username/password. This endpoint becomes unavailable once an administrator exists. Use `POST /auth/login` to receive a bearer token. Only administrators may create users at `POST /admin/users`.

## Product catalogue

Run `python -m alembic upgrade head` to apply all migrations through `20261107_04`. Products use decimal-safe prices, optional MRP/category/SKU fields, a unit of measure, optional pack size, active status, and an explicit tax-classification status (`unknown`, `unverified`, or `verified`). The application does not infer GST classification or rates.

Barcodes are separate globally unique records. They are kept exactly as supplied, including leading zeroes. Each value must contain 1-128 non-whitespace, non-control characters; no universal length or checksum rule is applied. A product can be inactive and retains its barcodes, making scans and future historical references unambiguous.

All product routes require a bearer token. Administrators may create, update, and deactivate products; administrators, cashiers, and viewers may read products. `purchase_price` is write-only through the administrator-only routes and is not included in catalogue responses.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/products` | Create a product (administrator) |
| `GET /api/products` | List/search products with `search`, `active`, `limit`, and `offset` (all authenticated roles) |
| `GET /api/products/{product_id}` | Read one product (all authenticated roles) |
| `PATCH /api/products/{product_id}` | Update product data or append barcodes (administrator) |
| `POST /api/products/{product_id}/deactivate` | Mark a product inactive without deletion (administrator) |

## Checkout search and sale snapshots

`GET /api/products/search?query=...&category=...` is available to all authenticated catalogue roles. It returns active products only, with exact barcode matches first, then exact SKU, partial barcode, partial SKU, and case-insensitive partial-name matches. Each result includes name, SKU, unit, MRP, selling price, category, and a `match_type`. The endpoint always returns every matching product so the operator can select the intended line; it never selects an ambiguous match automatically. Products without barcodes remain searchable by SKU or name and can be added to a sale.

Cashiers and administrators can create a draft through `POST /api/sales`, then add either a catalogue line with `POST /api/sales/{sale_id}/lines/catalogue` or a temporary line with `POST /api/sales/{sale_id}/lines/temporary`. Temporary lines require their own description, unit, quantity, price, and explicitly supplied tax-classification status. They never create or alter permanent catalogue products and do not inherit tax values from another product. `POST /api/sales/{sale_id}/issue` finalizes the draft; its product description, SKU, unit, MRP, price, and tax values are retained as line snapshots even when the catalogue later changes.

This repository does not yet contain a browser or mobile checkout client. Keyboard navigation, scanner input focus after cart additions, and rapid repeated scan behavior must be implemented in that client while it calls the search and sale endpoints above.

## Catalogue import preview

Administrators can import a UTF-8 CSV or `.xlsx` catalogue file through a two-step API. The server reads the upload for in-memory preview processing and closes the upload after parsing; it never stores the source export. Preview validates the file and creates no products. A successful preview stores only normalized proposed rows, validation counts, a source digest, and an opaque one-time confirmation secret for 15 minutes.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/product-imports/preview` | Validate a `.csv` or `.xlsx` file (administrator) |
| `POST /api/product-imports/confirm` | Create the server-stored proposed products using `confirmation_token` (administrator who created the preview) |

The first row must use these column names: `name`, `unit_of_measure`, and `selling_price`. Optional columns are `description`, `sku`, `barcodes`, `pack_size`, `purchase_price`, `tax_classification`, and `tax_classification_status`. Put multiple barcodes in one CSV cell separated by semicolons. The preview response includes proposed additions, rejected-row and conflict counts, and row-level errors. It issues no confirmation token when any error exists.

CSV files must be UTF-8. For Excel, format `sku` and `barcodes` cells as **Text** before entering values. Numeric Excel identifier cells are rejected, because Excel may already have removed leading zeroes. The import does not infer tax classifications or GST rates.

Confirmation accepts only the preview creator's unused, unexpired secret. It revalidates the stored canonical product data against the current catalogue before creating products. The product inserts and audit update use one transaction: all accepted rows are committed together or none are created. A confirmed or failed preview clears its temporary proposed rows; its audit summary retains the creator, confirmer, timestamps, digest, and accepted/rejected/conflict/created counts. Repeating a confirmation cannot create duplicate products.

## Payments, receipts, and khata

Administrators configure a receipt series for each financial year with `POST /api/receipt-series`. Issue a draft sale using `POST /api/sales/{sale_id}/issue`, an `Idempotency-Key` header, and the configured series. The allocated public number has the form `SERIES/2026-27/000001`; it is separate from the internal sale ID. This is an operational numbering format only and has not been validated as a GST invoice policy.

Record each cash, UPI, card, bank-transfer, or khata component separately with `POST /api/sales/{sale_id}/payments` and an `Idempotency-Key`. The server prevents overpayment, returns the original payment for an identical retry, and rejects reuse of a key with different data. Khata components require a customer and create a ledger debit; they are not money received. `GET /api/sales/{sale_id}/payments` returns the breakdown and `GET /api/receipts/{receipt_number}` retrieves a receipt without writing any new records.

Create customers with `POST /api/customers`; optional opening balances are explicit, unverified ledger debits. `POST /api/customers/{customer_id}/repayments` creates an append-only credit ledger entry, and `GET /api/customers/{customer_id}/statement` computes the current outstanding balance from entries. Ledger debit increases what the customer owes; ledger credit decreases it. No posted payment or ledger entry is edited or deleted.

Administrators record controlled corrections through `POST /api/customers/{customer_id}/adjustments`, with an idempotency key, direction, amount, meaningful reason, and optional ledger-entry reference. Reversals use `POST /api/customers/{customer_id}/ledger/{entry_id}/reversals`; they create one linked, opposite-direction compensating entry and never alter the original. Statements accept inclusive `start_date` and `end_date` (`YYYY-MM-DD`) interpreted in Asia/Kolkata, and `limit`/`offset`. Each page carries balances calculated across the complete selected period, so a displayed running balance remains correct when paginated.

The server binds to loopback by default in this development command. Do not expose it to the public internet. Local network access, authentication, persistent database storage, backups, and production startup/recovery procedures must be implemented before shop use.

### Finance operations page

Open `/operations` on the local POS host and sign in with a POS account. The page provides recent payment history, customer statements, receipt presentation (sale-time line snapshots, rounded line totals, total, payments, and balance due), and ledger reversal review. Administrators can record adjustments and compensating reversals; cashiers and viewers can use the read-only views. Every data request is enforced by the authenticated API, so hiding controls in the page is not the authorization boundary. Payment history and customer lists are paginated/loaded through authenticated API routes; the page currently displays the latest 100 payment records and up to 500 statement entries. This operational page is a local internal tool, not a GST invoice template or an externally exposed service.

Additional finance reads: `GET /api/payment-history?limit=100&offset=0` lists payment events, and `GET /api/customers` lists customer choices for the statement view. Receipt retrieval now also includes `total`, `received_total` (cash and non-khata methods), `khata_total`, `settled_total`, `balance_due`, per-line `line_total`, and the payment breakdown alongside the existing immutable sale snapshots. Khata is reported separately because it is customer credit, not money received.

## Planned architecture

Start as a modular FastAPI application with SQLite for a single counter PC, versioned Alembic migrations, SQLAlchemy persistence, and a responsive React/Vite client. Keep runtime database files, uploaded supplier documents, backups, and secrets outside this source directory. Consider PostgreSQL only when measured concurrent use requires it. Android offline billing is a later phase requiring a durable local database, an outbox, idempotent sync, and reconciliation.

## Safety and migration

Development and tests must use synthetic data. Never point this application at the BUSY database or modify original BUSY exports. Historical stock is unverified until reconciled. Continue operating BUSY alongside the POS until invoice totals, barcodes, prices, and customer balances pass agreed acceptance checks and a rollback plan is documented.

## Next steps

Continue with a finance UI/API review using synthetic accounts and data: confirm role behavior for administrator, cashier, and viewer; exercise payment-history pagination and statement date filters; and verify adjustment/reversal recovery after interrupted requests. Then prioritize the first counter checkout client (scanner focus, keyboard flow, and clear amount received versus khata) and operational readiness (persistent database location, backup/restore rehearsal, startup/recovery procedure, and reconciliation plan). Do not migrate live BUSY data, claim GST compliance, or expose this development server publicly during these steps.

New-chat handoff: “Continue Jaya Surabhi POS from the ledger adjustments milestone. Review AGENTS.md, README.md, migration `20261108_06`, and the latest financial tests before changes.”
