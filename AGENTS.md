# Jaya Surabhi Super Market POS - Engineering Rules

These rules apply to all work in this repository. Read the existing project structure, documentation, configuration, migrations, and tests before making changes. Preserve the existing stack and architecture unless a change is justified, documented, and made incrementally.

## Business data and migration safety

- BUSY remains the system of record until migration from copied exports, testing, and reconciliation are complete.
- Never modify the live BUSY database, original BUSY exports, or any production source data.
- Use only synthetic data in development and automated tests.
- Do not treat imported historical stock as verified until it has been reconciled.
- Do not claim GST compliance until applicable requirements and the business configuration have been verified.

## Data integrity and financial rules

- Use SQLAlchemy and Alembic for all database schema changes; migrations must be versioned and reviewable.
- Use `Decimal`-safe monetary calculations. Validate business rules on the backend, even when the client validates input too.
- Preserve barcode strings exactly, including leading zeroes.
- Preserve issued sale and receipt snapshots, including product, price, discount, tax, payment, and override details, so historical records remain accurate after catalogue changes.
- Retain financial history in immutable or append-only records where appropriate. Use transactional writes for sales, payments, and related ledger entries.
- Never auto-merge similar products. Never silently assign, inherit, or change a product's tax classification.
- Use stable identifiers and idempotency controls where retries could create duplicate bills, payments, or synchronization operations.

## Security and repository hygiene

- Protect credentials, production databases, customer records, copied exports, uploaded documents, and backups from Git. Keep them outside the source directory where practical and maintain appropriate `.gitignore` entries.
- Never print secret values in logs, test output, documentation, or task reports.
- Expose only authenticated APIs. Enforce authorization on the server for every protected action and for remote read-only access.
- Do not expose the shop PC directly to the public internet.

## Delivery and quality

- Make the smallest coherent change that advances the current phase. Preserve existing work and do not delete, reset, or overwrite user work without explicit authorization.
- Keep business rules in backend services rather than relying solely on frontend behavior.
- Write automated tests for business logic and expected failure cases. Run relevant checks after changes and report results exactly; never claim a test or deployment passed unless it was actually verified.
- Document material decisions, operational recovery steps, and known limitations concisely.

## Future scope

- Keep Android offline billing, durable local storage, device identity, queued synchronization, idempotent retry, conflict reconciliation, and invoice-series allocation in scope for later phases.
- Keep authenticated, encrypted remote read-only access in scope for later phases. Do not represent cached web access as independent offline billing.
