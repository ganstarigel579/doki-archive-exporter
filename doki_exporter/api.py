"""HTTP client for the public Astral Doki integration API."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable


DEFAULT_BASE_URL = "https://api.1cdocs.ru"
PAGE_SIZE = 100
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


class DokiError(RuntimeError):
    """An API or network error safe to display to a console user."""


@dataclass
class Response:
    body: bytes
    headers: Any
    status: int


class DokiClient:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout

    def request(
        self,
        path: str,
        *,
        abonent_id: str | None = None,
        query: dict[str, Any] | None = None,
        accept: str = "application/json",
        attempts: int = 5,
    ) -> Response:
        url = f"{self.base_url}{path}"
        if query:
            values = {key: value for key, value in query.items() if value is not None}
            url += "?" + urllib.parse.urlencode(values, doseq=True)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": accept}
        if abonent_id:
            headers["abonentId"] = abonent_id
        request = urllib.request.Request(url, headers=headers, method="GET")

        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as result:
                    return Response(result.read(), result.headers, result.status)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:1000]
                if exc.code in RETRYABLE_CODES and attempt < attempts:
                    delay = min(2 ** (attempt - 1), 16)
                    retry_after = exc.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = min(max(delay, int(retry_after)), 60)
                    print(
                        f"      Сервер вернул HTTP {exc.code}; повтор "
                        f"{attempt + 1}/{attempts} через {delay} сек…",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                hint = ""
                if exc.code == 401:
                    hint = " Проверьте токен: API ожидает Bearer access token."
                raise DokiError(f"HTTP {exc.code} для {path}: {body}{hint}") from exc
            except urllib.error.URLError as exc:
                if attempt < attempts:
                    delay = min(2 ** (attempt - 1), 16)
                    print(f"      Ошибка сети; повтор {attempt + 1}/{attempts} через {delay} сек…")
                    time.sleep(delay)
                    continue
                raise DokiError(f"Ошибка сети для {path}: {exc.reason}") from exc
        raise AssertionError("unreachable")

    def get_json(self, path: str, **kwargs: Any) -> Any:
        response = self.request(path, **kwargs)
        try:
            return json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise DokiError(f"API вернул некорректный JSON для {path}") from exc

    def all_abonents(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        offset = 0
        while True:
            page = self.get_json("/api/v1/abonents", query={"offset": offset, "count": PAGE_SIZE})
            data = page.get("data") or []
            fresh = [item for item in data if str(item.get("id")) not in seen_ids]
            if not fresh:
                return result
            result.extend(fresh)
            seen_ids.update(str(item.get("id")) for item in fresh)
            offset += len(data)

    def iter_packages(
        self, abonent_id: str, direction: str, date_from: str, date_to: str
    ) -> Iterable[dict[str, Any]]:
        offset = 0
        seen_ids: set[str] = set()
        while True:
            page = self.get_json(
                f"/async/v1/Packages/{direction}",
                abonent_id=abonent_id,
                query={
                    "from": date_from,
                    "to": date_to,
                    "offset": offset,
                    "count": PAGE_SIZE,
                    "onlyNotViewed": "false",
                },
            )
            packages = page.get("packages") or []

            def package_key(item: dict[str, Any]) -> str:
                if item.get("id"):
                    return str(item["id"])
                ids = sorted(str(flow.get("id")) for flow in item.get("docflowSummaries") or [])
                return "flows:" + ",".join(ids)

            fresh = [item for item in packages if package_key(item) not in seen_ids]
            if not fresh:
                return
            yield from fresh
            seen_ids.update(package_key(item) for item in fresh)
            offset += len(packages)
