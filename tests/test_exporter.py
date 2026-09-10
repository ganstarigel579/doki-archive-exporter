import io
import tempfile
import unittest
import zipfile
from datetime import date
from email.message import Message
from pathlib import Path

from doki_exporter.api import Response
from doki_exporter.exporter import (
    api_period,
    collect_suppliers,
    export_docflow,
    safe_name,
)


def zip_bytes() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("document.xml", "<xml/>")
    return stream.getvalue()


class FakeClient:
    def __init__(self):
        self.calls = 0

    def get_json(self, path, **kwargs):
        self.calls += 1
        return {"path": path}

    def request(self, path, **kwargs):
        self.calls += 1
        body = zip_bytes() if path.endswith("/files") else b"%PDF-test"
        return Response(body, Message(), 200)


class NoNetworkClient:
    def get_json(self, path, **kwargs):
        raise AssertionError("JSON must not be downloaded again")

    def request(self, path, **kwargs):
        raise AssertionError("Artifact must not be downloaded again")


class PackageClient:
    def iter_packages(self, abonent_id, direction, date_from, date_to):
        self.args = (abonent_id, direction, date_from, date_to)
        supplier = {"id": "s1", "name": "ООО Поставщик", "inn": "7800000000", "kpp": None}
        return iter(
            [
                {
                    "id": "p1",
                    "docflowSummaries": [
                        {"id": "d1", "owner": supplier, "counterparty": {"inn": "7700000000"}},
                        {"id": "d2", "owner": supplier, "counterparty": {"inn": "7700000000"}},
                    ],
                }
            ]
        )


class ExporterTests(unittest.TestCase):
    def test_period_uses_full_moscow_days(self):
        self.assertEqual(
            api_period(date(2024, 1, 1), date(2025, 12, 31)),
            ("2024-01-01T00:00:00+03:00", "2025-12-31T23:59:59+03:00"),
        )

    def test_safe_name(self):
        self.assertEqual(safe_name('УПД № 1/2025:*?'), "УПД № 1_2025")

    def test_collects_supplier_and_counts_docflows(self):
        client = PackageClient()
        suppliers = collect_suppliers(
            client,
            {"id": "a1", "inn": "7700000000"},
            "2024-01-01T00:00:00+03:00",
            "2025-12-31T23:59:59+03:00",
        )
        self.assertEqual(len(suppliers), 1)
        self.assertEqual(suppliers[0]["inn"], "7800000000")
        self.assertEqual(len(suppliers[0]["docflows"]), 2)

    def test_resume_does_not_download_existing_files(self):
        abonent = {"id": "a1", "inn": "7700000000"}
        package = {"id": "p1"}
        flow = {
            "id": "d1",
            "primaryDocumentId": "x1",
            "name": "УПД №1",
            "creationDate": "2025-01-01T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = export_docflow(FakeClient(), root, abonent, package, flow)
            second = export_docflow(NoNetworkClient(), root, abonent, package, flow)
            self.assertEqual(first["exportStatus"], "complete")
            self.assertEqual(second["exportStatus"], "complete")
            self.assertTrue(
                all(item["status"] == "existing" for item in second["artifacts"].values())
            )


if __name__ == "__main__":
    unittest.main()
