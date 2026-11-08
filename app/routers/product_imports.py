"""Validated, one-time product catalogue import previews."""

import csv
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_session
from app.dependencies import require_roles
from app.models import ImportPreviewStatus, Product, ProductBarcode, ProductImportPreviewRecord, User, UserRole
from app.schemas import ConfirmProductImport, ImportProductProposal, ImportRowError, ProductCreate, ProductImportPreview, ProductImportResult

router = APIRouter(prefix="/api/product-imports", tags=["product imports"])

MAX_FILE_BYTES = 2 * 1024 * 1024
PREVIEW_TTL = timedelta(minutes=15)
REQUIRED_COLUMNS = {"name", "unit_of_measure", "selling_price"}
OPTIONAL_COLUMNS = {"description", "sku", "barcodes", "pack_size", "purchase_price", "tax_classification", "tax_classification_status"}
ALLOWED_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS


@dataclass
class ParsedImport:
    candidates: list[tuple[int, ProductCreate]]
    errors: list[ImportRowError]
    total_rows: int


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _counts(parsed: ParsedImport) -> tuple[int, int]:
    return len({error.row_number for error in parsed.errors}), len({error.row_number for error in parsed.errors if error.kind == "conflict"})


def _cell_to_text(value: Any, column: str, row_number: int, errors: list[ImportRowError]) -> str | None:
    if value is None:
        return None
    if column in {"sku", "barcodes"} and isinstance(value, (int, float)) and not isinstance(value, bool):
        errors.append(ImportRowError(row_number=row_number, field=column, message="Excel barcode and SKU cells must be formatted as text to preserve their exact value."))
        return None
    return str(value)


def _parse_rows(rows: list[dict[str, Any]]) -> ParsedImport:
    errors: list[ImportRowError] = []
    candidates: list[tuple[int, ProductCreate]] = []
    seen_skus: dict[str, int] = {}
    seen_barcodes: dict[str, int] = {}
    for row_number, raw_row in enumerate(rows, start=2):
        row_error_start = len(errors)
        raw = {key: _cell_to_text(value, key, row_number, errors) for key, value in raw_row.items()}
        if all(value in (None, "") for value in raw.values()):
            continue
        payload = {
            "name": raw.get("name"), "description": raw.get("description") or None, "sku": raw.get("sku") or None,
            "barcodes": [] if not raw.get("barcodes") else raw["barcodes"].split(";"),
            "unit_of_measure": raw.get("unit_of_measure"), "pack_size": raw.get("pack_size") or None,
            "selling_price": raw.get("selling_price"), "purchase_price": raw.get("purchase_price") or None,
            "tax_classification": raw.get("tax_classification") or None,
            "tax_classification_status": raw.get("tax_classification_status") or "unknown",
        }
        try:
            candidate = ProductCreate.model_validate(payload)
        except ValidationError as exc:
            for error in exc.errors():
                field = error["loc"][0] if error["loc"] else None
                errors.append(ImportRowError(row_number=row_number, field=str(field) if field else None, message=error["msg"]))
            continue
        if len(errors) != row_error_start:
            continue
        if candidate.sku is not None:
            if candidate.sku in seen_skus:
                errors.append(ImportRowError(row_number=row_number, field="sku", message=f"Duplicates row {seen_skus[candidate.sku]} in this file.", kind="conflict"))
            else:
                seen_skus[candidate.sku] = row_number
        for barcode in candidate.barcodes:
            if barcode in seen_barcodes:
                errors.append(ImportRowError(row_number=row_number, field="barcodes", message=f"Barcode duplicates row {seen_barcodes[barcode]} in this file.", kind="conflict"))
            else:
                seen_barcodes[barcode] = row_number
        if len(errors) == row_error_start:
            candidates.append((row_number, candidate))
    return ParsedImport(candidates, errors, len(rows))


def _validate_headers(headers: list[str]) -> None:
    header_set = set(headers)
    unknown, missing = header_set - ALLOWED_COLUMNS, REQUIRED_COLUMNS - header_set
    if unknown or missing or len(header_set) != len(headers):
        parts = []
        if missing:
            parts.append(f"Missing required columns: {', '.join(sorted(missing))}.")
        if unknown:
            parts.append(f"Unsupported columns: {', '.join(sorted(unknown))}.")
        if len(header_set) != len(headers):
            parts.append("Duplicate column names are not allowed.")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=" ".join(parts))


def _read_import(upload: UploadFile, content: bytes) -> ParsedImport:
    filename = (upload.filename or "").lower()
    if filename.endswith(".csv"):
        try:
            reader = csv.DictReader(StringIO(content.decode("utf-8-sig")))
        except UnicodeDecodeError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="CSV files must be UTF-8 encoded.")
        _validate_headers(list(reader.fieldnames or []))
        return _parse_rows(list(reader))
    if filename.endswith(".xlsx"):
        workbook = None
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            values = workbook.active.iter_rows(values_only=True)
            headers = next(values, None)
            if headers is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Spreadsheet has no header row.")
            names = [str(header) if header is not None else "" for header in headers]
            _validate_headers(names)
            return _parse_rows([dict(zip(names, row, strict=False)) for row in values])
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid .xlsx file.")
        finally:
            if workbook is not None:
                workbook.close()
    raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a .csv or .xlsx file.")


def _with_database_conflicts(parsed: ParsedImport, session: Session) -> ParsedImport:
    skus = {candidate.sku for _, candidate in parsed.candidates if candidate.sku is not None}
    barcodes = {barcode for _, candidate in parsed.candidates for barcode in candidate.barcodes}
    existing_skus = set(session.scalars(select(Product.sku).where(Product.sku.in_(skus))).all()) if skus else set()
    existing_barcodes = set(session.scalars(select(ProductBarcode.value).where(ProductBarcode.value.in_(barcodes))).all()) if barcodes else set()
    candidates, errors = [], list(parsed.errors)
    for number, candidate in parsed.candidates:
        conflicts = []
        if candidate.sku in existing_skus:
            conflicts.append("SKU already exists")
        if any(barcode in existing_barcodes for barcode in candidate.barcodes):
            conflicts.append("barcode already exists")
        if conflicts:
            errors.append(ImportRowError(row_number=number, message=" and ".join(conflicts) + ".", kind="conflict"))
        else:
            candidates.append((number, candidate))
    return ParsedImport(candidates, errors, parsed.total_rows)


async def _parse_upload(upload: UploadFile) -> tuple[bytes, ParsedImport]:
    try:
        content = await upload.read(MAX_FILE_BYTES + 1)
    finally:
        await upload.close()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Import file is empty.")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Import file exceeds the 2 MiB limit.")
    return content, _read_import(upload, content)


def _cleanup_expired_pending_previews(session: Session) -> None:
    session.execute(delete(ProductImportPreviewRecord).where(
        ProductImportPreviewRecord.status == ImportPreviewStatus.PENDING.value,
        ProductImportPreviewRecord.expires_at < _utcnow(),
    ))


def _proposal(row: int, candidate: ProductCreate) -> ImportProductProposal:
    return ImportProductProposal(row_number=row, name=candidate.name, sku=candidate.sku, barcodes=candidate.barcodes,
        unit_of_measure=candidate.unit_of_measure, pack_size=candidate.pack_size, selling_price=candidate.selling_price,
        tax_classification=candidate.tax_classification, tax_classification_status=candidate.tax_classification_status)


def _close_failed(record: ProductImportPreviewRecord, session: Session) -> None:
    record.status, record.proposed_rows, record.created_count = ImportPreviewStatus.FAILED.value, [], 0
    session.commit()


def _record_from_token(token: str, user: User, session: Session) -> ProductImportPreviewRecord:
    try:
        record_id, secret = token.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid import confirmation.")
    record = session.get(ProductImportPreviewRecord, record_id)
    supplied_hash = hashlib.sha256(secret.encode()).hexdigest()
    if record is None or not hmac.compare_digest(record.confirmation_secret_hash, supplied_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid import confirmation.")
    if record.created_by_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the preview creator may confirm this import.")
    if record.status != ImportPreviewStatus.PENDING.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This preview has already been used or closed.")
    if record.expires_at < _utcnow():
        _close_failed(record, session)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This preview has expired; upload the file again.")
    return record


@router.post("/preview", response_model=ProductImportPreview)
async def preview_product_import(file: UploadFile = File(...), session: Session = Depends(get_session), user: User = Depends(require_roles(UserRole.ADMIN))) -> ProductImportPreview:
    content, parsed = await _parse_upload(file)
    _cleanup_expired_pending_previews(session)
    parsed = _with_database_conflicts(parsed, session)
    rejected_count, conflict_count = _counts(parsed)
    proposals = [_proposal(row, candidate) for row, candidate in parsed.candidates]
    token = None
    if proposals and not parsed.errors:
        secret = secrets.token_urlsafe(32)
        record = ProductImportPreviewRecord(
            confirmation_secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
            created_by_user_id=user.id, content_digest=hashlib.sha256(content).hexdigest(),
            proposed_rows=[candidate.model_dump(mode="json") for _, candidate in parsed.candidates],
            accepted_count=len(proposals), rejected_count=rejected_count, conflict_count=conflict_count,
            status=ImportPreviewStatus.PENDING.value, expires_at=_utcnow() + PREVIEW_TTL,
        )
        session.add(record)
        session.flush()
        token = f"{record.id}.{secret}"
    session.commit()
    return ProductImportPreview(total_rows=parsed.total_rows, valid_rows=len(proposals), rejected_rows=rejected_count,
        conflicting_rows=conflict_count, proposed_additions=proposals, errors=parsed.errors, confirmation_token=token)


@router.post("/confirm", response_model=ProductImportResult, status_code=status.HTTP_201_CREATED)
def confirm_product_import(payload: ConfirmProductImport, session: Session = Depends(get_session), user: User = Depends(require_roles(UserRole.ADMIN))) -> ProductImportResult:
    record = _record_from_token(payload.confirmation_token, user, session)
    try:
        parsed = ParsedImport([(index + 2, ProductCreate.model_validate(value)) for index, value in enumerate(record.proposed_rows)], [], record.accepted_count)
    except ValidationError:
        _close_failed(record, session)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stored preview is invalid; upload the file again.")
    parsed = _with_database_conflicts(parsed, session)
    rejected_count, conflict_count = _counts(parsed)
    if parsed.errors or len(parsed.candidates) != record.accepted_count:
        record.rejected_count += rejected_count
        record.conflict_count += conflict_count
        _close_failed(record, session)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Catalogue changed since preview; upload the file again.")
    for _, candidate in parsed.candidates:
        session.add(Product(name=candidate.name, description=candidate.description, sku=candidate.sku,
            unit_of_measure=candidate.unit_of_measure, pack_size=candidate.pack_size, selling_price=candidate.selling_price,
            purchase_price=candidate.purchase_price, tax_classification=candidate.tax_classification,
            tax_classification_status=candidate.tax_classification_status.value,
            barcodes=[ProductBarcode(value=value) for value in candidate.barcodes]))
    record.status, record.confirmed_by_user_id, record.confirmed_at = ImportPreviewStatus.CONFIRMED.value, user.id, _utcnow()
    record.created_count, record.proposed_rows = record.accepted_count, []
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        retry_record = session.get(ProductImportPreviewRecord, record.id)
        if retry_record is not None:
            _close_failed(retry_record, session)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Catalogue changed during confirmation; no products were created.")
    return ProductImportResult(created_count=record.created_count, rejected_count=record.rejected_count, conflicting_count=record.conflict_count)
