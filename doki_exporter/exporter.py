"""Discovery, filtering and resumable export operations."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .api import DokiClient, DokiError


ARTIFACT_LABELS = {
    "details": "карточка",
    "transactions": "транзакции",
    "history": "история",
    "documentPdf": "PDF документа",
    "protocol": "протокол",
    "files": "ZIP-архив",
}


def safe_name(value: str, limit: int = 100) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return (value or "без_названия")[:limit]


def api_period(date_from: date, date_to: date) -> tuple[str, str]:
    return (
        f"{date_from.isoformat()}T00:00:00+03:00",
        f"{date_to.isoformat()}T23:59:59+03:00",
    )


def flatten_docflows(
    packages: Iterable[dict[str, Any]],
) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for package in packages:
        for docflow in package.get("docflowSummaries") or []:
            yield package, docflow


def incoming_sender(
    docflow: dict[str, Any], account_company: dict[str, Any]
) -> dict[str, Any] | None:
    account_inn = str(account_company.get("inn", ""))
    owner = docflow.get("owner") or {}
    counterparty = docflow.get("counterparty") or {}
    if owner and str(owner.get("inn", "")) != account_inn:
        return owner
    if counterparty and str(counterparty.get("inn", "")) != account_inn:
        return counterparty
    return None


def supplier_key(supplier: dict[str, Any]) -> str:
    inn = str(supplier.get("inn", ""))
    kpp = str(supplier.get("kpp", ""))
    if inn:
        return f"inn:{inn}:{kpp}"
    if supplier.get("id"):
        return f"id:{supplier['id']}"
    return f"name:{str(supplier.get('name', '')).casefold()}"


def collect_suppliers(
    client: DokiClient,
    account_company: dict[str, Any],
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    suppliers: dict[str, dict[str, Any]] = {}
    seen_docflows: set[str] = set()
    packages = client.iter_packages(str(account_company["id"]), "incoming", date_from, date_to)
    for package, docflow in flatten_docflows(packages):
        docflow_id = str(docflow.get("id", ""))
        if not docflow_id or docflow_id in seen_docflows:
            continue
        seen_docflows.add(docflow_id)
        sender = incoming_sender(docflow, account_company)
        if not sender:
            continue
        key = supplier_key(sender)
        entry = suppliers.setdefault(
            key,
            {
                "id": sender.get("id"),
                "name": sender.get("name") or "Без названия",
                "inn": sender.get("inn"),
                "kpp": sender.get("kpp"),
                "docflows": [],
            },
        )
        entry["docflows"].append((package, docflow))
    return sorted(suppliers.values(), key=lambda item: str(item["name"]).casefold())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_artifact(path: Path, *, validate_json: bool = False) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    if validate_json:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
    elif path.suffix.lower() == ".zip" and not zipfile.is_zipfile(path):
        return None
    elif path.suffix.lower() == ".pdf":
        try:
            with path.open("rb") as stream:
                if not stream.read(5).startswith(b"%PDF-"):
                    return None
        except OSError:
            return None
    return {
        "path": path.name,
        "status": "existing",
        "size": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def valid_download(body: bytes, target: Path) -> bool:
    if not body:
        return False
    if target.suffix.lower() == ".pdf":
        return body.startswith(b"%PDF-")
    if target.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                return archive.testzip() is None
        except zipfile.BadZipFile:
            return False
    return True


def download_artifact(
    client: DokiClient,
    endpoint: str,
    target: Path,
    abonent_id: str | None,
) -> dict[str, Any]:
    existing = existing_artifact(target)
    if existing:
        return existing
    try:
        response = client.request(
            endpoint, abonent_id=abonent_id, accept="*/*", attempts=3
        )
    except DokiError as exc:
        return {"path": target.name, "status": "skipped", "error": str(exc)}
    if not valid_download(response.body, target):
        return {
            "path": target.name,
            "status": "skipped",
            "error": "Сервер вернул пустой файл или данные неожиданного формата",
        }
    target.write_bytes(response.body)
    return {
        "path": target.name,
        "status": "downloaded",
        "size": len(response.body),
        "sha256": hashlib.sha256(response.body).hexdigest(),
        "contentType": response.headers.get("Content-Type"),
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def export_docflow(
    client: DokiClient,
    root: Path,
    abonent: dict[str, Any],
    package: dict[str, Any],
    docflow: dict[str, Any],
) -> dict[str, Any]:
    docflow_id = str(docflow["id"])
    created = str(docflow.get("creationDate") or package.get("creationDate") or "без_даты")[:10]
    flow_dir = root / "incoming" / f"{created}_{safe_name(str(docflow.get('name', 'документ')), 70)}_{docflow_id}"
    flow_dir.mkdir(parents=True, exist_ok=True)
    write_json(flow_dir / "summary.json", {"package": package, "docflow": docflow})

    artifacts: dict[str, Any] = {}
    json_endpoints = {
        "details": f"/async/v1/Docflows/{docflow_id}",
        "transactions": f"/async/v1/Docflows/{docflow_id}/transactions",
        "history": f"/async/v1/Docflows/{docflow_id}/history",
    }
    for name, endpoint in json_endpoints.items():
        target = flow_dir / f"{name}.json"
        existing = existing_artifact(target, validate_json=True)
        if existing:
            artifacts[name] = existing
            continue
        try:
            value = client.get_json(endpoint, abonent_id=str(abonent["id"]), attempts=3)
            write_json(target, value)
            artifacts[name] = {
                "path": target.name,
                "status": "downloaded",
                "size": target.stat().st_size,
                "sha256": file_sha256(target),
            }
        except DokiError as exc:
            artifacts[name] = {"path": target.name, "status": "skipped", "error": str(exc)}

    primary_document_id = docflow.get("primaryDocumentId")
    if primary_document_id:
        artifacts["documentPdf"] = download_artifact(
            client,
            f"/async/v1/Docflows/documents/{primary_document_id}/visualization",
            flow_dir / "document.pdf",
            None,
        )
    artifacts["protocol"] = download_artifact(
        client,
        f"/async/v1/Docflows/{docflow_id}/protocol",
        flow_dir / "protocol.pdf",
        str(abonent["id"]),
    )
    artifacts["files"] = download_artifact(
        client,
        f"/async/v1/Docflows/{docflow_id}/files",
        flow_dir / "documents.zip",
        str(abonent["id"]),
    )

    export_status = (
        "complete"
        if all(item.get("status") in {"downloaded", "existing"} for item in artifacts.values())
        else "incomplete"
    )
    result = {
        "abonentInn": abonent.get("inn"),
        "abonentName": abonent.get("shortName"),
        "direction": "incoming",
        "packageId": package.get("id"),
        "docflowId": docflow_id,
        "creationDate": docflow.get("creationDate") or package.get("creationDate"),
        "name": docflow.get("name"),
        "status": docflow.get("status"),
        "exportStatus": export_status,
        "ownerInn": (docflow.get("owner") or {}).get("inn"),
        "counterpartyInn": (docflow.get("counterparty") or {}).get("inn"),
        "directory": str(flow_dir),
        "artifacts": artifacts,
    }
    write_json(flow_dir / "manifest.json", result)
    return result


def export_supplier(
    client: DokiClient,
    account_company: dict[str, Any],
    supplier: dict[str, Any],
    output: Path,
    period: tuple[date, date],
) -> tuple[int, int, Path]:
    supplier_part = f"{supplier.get('inn') or 'без_инн'}_{safe_name(str(supplier.get('name', 'поставщик')))}"
    account_part = f"{account_company['inn']}_{safe_name(str(account_company.get('shortName', 'организация')))}"
    export_root = output / f"{period[0].isoformat()}_{period[1].isoformat()}" / account_part / supplier_part
    export_root.mkdir(parents=True, exist_ok=True)
    results_by_id: dict[str, dict[str, Any]] = {}
    failed_jobs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def run_job(package: dict[str, Any], docflow: dict[str, Any], retry: bool = False) -> bool:
        docflow_id = str(docflow.get("id", ""))
        prefix = "повтор" if retry else f"{len(results_by_id) + 1}/{len(supplier['docflows'])}"
        print(f"  [{prefix}] {docflow.get('creationDate', 'без даты')} — {docflow.get('name', 'без названия')}")
        result = export_docflow(client, export_root, account_company, package, docflow)
        results_by_id[docflow_id] = result
        missing = [
            ARTIFACT_LABELS.get(name, name)
            for name, artifact in result["artifacts"].items()
            if artifact.get("status") == "skipped"
        ]
        if missing:
            print(f"      ⚠ не удалось получить: {', '.join(missing)}")
            return False
        downloaded = [
            ARTIFACT_LABELS.get(name, name)
            for name, artifact in result["artifacts"].items()
            if artifact.get("status") == "downloaded"
        ]
        if downloaded:
            print(f"      ✓ сохранено: {', '.join(downloaded)}")
        else:
            print("      ↷ всё уже скачано")
        return True

    print(f"\nВыгружаю: {supplier.get('name')} ({supplier.get('inn') or 'без ИНН'})")
    for package, docflow in supplier["docflows"]:
        if not run_job(package, docflow):
            failed_jobs.append((package, docflow))
    if failed_jobs:
        print(f"\nПовторно скачиваю недостающие элементы: {len(failed_jobs)}")
        for package, docflow in failed_jobs:
            run_job(package, docflow, retry=True)

    results = list(results_by_id.values())
    errors = sum(item.get("exportStatus") != "complete" for item in results)
    write_json(
        export_root / "export_manifest.json",
        {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "period": {"from": period[0].isoformat(), "to": period[1].isoformat()},
            "accountCompany": account_company,
            "supplier": {key: value for key, value in supplier.items() if key != "docflows"},
            "docflows": results,
        },
    )
    fields = [
        "abonentInn", "direction", "creationDate", "docflowId", "name", "status",
        "exportStatus", "ownerInn", "counterpartyInn", "directory", "error",
    ]
    with (export_root / "export_index.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", delimiter=";")
        writer.writeheader()
        writer.writerows(results)
    return len(results) - errors, errors, export_root
