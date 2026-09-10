"""Russian-language interactive console interface."""

from __future__ import annotations

import getpass
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .api import DEFAULT_BASE_URL, DokiClient, DokiError
from .exporter import api_period, collect_suppliers, export_supplier


TITLE = "Доки — выгрузка документооборота"
DEFAULT_START = date(2024, 1, 1)
DEFAULT_END = date(2025, 12, 31)


def line(char: str = "─") -> None:
    print(char * 76)


def ask_token() -> str:
    existing = os.environ.get("DOKI_ACCESS_TOKEN", "").strip()
    if existing:
        answer = input("Использовать токен из DOKI_ACCESS_TOKEN? [Д/н]: ").strip().lower()
        if answer in {"", "д", "да", "y", "yes"}:
            return existing
    while True:
        token = getpass.getpass("Вставьте API-токен (ввод скрыт): ").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token:
            return token
        print("Токен не может быть пустым.")


def parse_date(value: str) -> date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("используйте формат ДД.ММ.ГГГГ")


def ask_date(prompt: str, default: date) -> date:
    while True:
        value = input(f"{prompt} [{default.strftime('%d.%m.%Y')}]: ").strip()
        if not value:
            return default
        try:
            return parse_date(value)
        except ValueError as exc:
            print(f"Некорректная дата: {exc}.")


def ask_period() -> tuple[date, date]:
    while True:
        print("\nУкажите период выгрузки (Enter оставляет значение по умолчанию).")
        start = ask_date("Дата начала", DEFAULT_START)
        end = ask_date("Дата окончания", DEFAULT_END)
        if start <= end:
            return start, end
        print("Дата начала не может быть позже даты окончания.")


def parse_number_selection(value: str, maximum: int) -> list[int] | None:
    if not value or not all(part.strip() for part in value.split(",")):
        return None
    selected: set[int] = set()
    try:
        for part in value.split(","):
            bounds = part.strip().split("-", 1)
            if len(bounds) == 1:
                selected.add(int(bounds[0]))
            else:
                start, end = map(int, bounds)
                if start > end:
                    return None
                selected.update(range(start, end + 1))
    except ValueError:
        return None
    if not selected or min(selected) < 1 or max(selected) > maximum:
        return None
    return sorted(selected)


def choose_many(
    items: list[dict[str, Any]],
    *,
    title: str,
    prompt: str,
    label: Callable[[dict[str, Any]], str],
    search_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not items:
        raise DokiError(f"{title}: список пуст.")
    current = items
    while True:
        line()
        print(title)
        for number, item in enumerate(current, 1):
            print(f"  {number:>3}. {label(item)}")
        line()
        print("Выбор: 1,3,5-7. Чтобы выбрать весь показанный список, введите: все")
        value = input(prompt).strip()
        if value.casefold() in {"все", "all"}:
            return current
        looks_like_inn = value.isdigit() and len(value) in {10, 12}
        numbers = None if looks_like_inn else parse_number_selection(value, len(current))
        if numbers:
            return [current[number - 1] for number in numbers]
        if not value:
            print("Введите номер, несколько номеров, название или ИНН.")
            continue
        needle = value.casefold()
        matches = [
            item
            for item in items
            if any(needle in str(item.get(field, "")).casefold() for field in search_fields)
        ]
        if not matches:
            print("Совпадений не найдено.")
            current = items
        elif len(matches) == 1:
            return matches
        else:
            current = matches


def company_label(company: dict[str, Any]) -> str:
    return (
        f"{company.get('shortName', 'Без названия')} | ИНН {company.get('inn') or '—'} "
        f"| КПП {company.get('kpp') or '—'}"
    )


def supplier_label(supplier: dict[str, Any]) -> str:
    return (
        f"{supplier.get('name', 'Без названия')} | ИНН {supplier.get('inn') or '—'} "
        f"| КПП {supplier.get('kpp') or '—'} | документов: {len(supplier['docflows'])}"
    )


def main() -> int:
    print()
    line("═")
    print(TITLE.center(76))
    line("═")
    print("Только чтение: программа не изменяет данные в Доки.")
    print("Токен хранится только в памяти текущего процесса.")

    try:
        token = ask_token()
        period = ask_period()
        api_from, api_to = api_period(*period)
        client = DokiClient(token, os.environ.get("DOKI_BASE_URL", DEFAULT_BASE_URL))

        print("\nПроверяю токен и загружаю юридические лица аккаунта…")
        companies = client.all_abonents()
        selected_companies = choose_many(
            companies,
            title="Юридические лица вашего аккаунта:",
            prompt="Номера, название или ИНН юрлиц: ",
            label=company_label,
            search_fields=("shortName", "fullName", "inn"),
        )

        selections: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for company in selected_companies:
            print(f"\nИщу поставщиков для: {company_label(company)}")
            suppliers = collect_suppliers(client, company, api_from, api_to)
            if not suppliers:
                print("  За выбранный период входящих документов не найдено.")
                continue
            selected_suppliers = choose_many(
                suppliers,
                title=f"Поставщики для {company.get('shortName')}:",
                prompt="Номера, название или ИНН поставщиков: ",
                label=supplier_label,
                search_fields=("name", "inn"),
            )
            selections.append((company, selected_suppliers))

        if not selections:
            raise DokiError("Нет выбранных документооборотов для выгрузки.")

        line()
        print(f"Период: {period[0].strftime('%d.%m.%Y')} — {period[1].strftime('%d.%m.%Y')}")
        for company, suppliers in selections:
            print(f"{company_label(company)}")
            for supplier in suppliers:
                print(f"    • {supplier_label(supplier)}")
        line()
        if input("Начать выгрузку? [д/Н]: ").strip().lower() not in {"д", "да", "y", "yes"}:
            print("Выгрузка отменена.")
            return 0

        output = Path(os.environ.get("DOKI_OUTPUT_DIR", "exports"))
        total_ok = 0
        total_errors = 0
        roots: list[Path] = []
        for company, suppliers in selections:
            for supplier in suppliers:
                ok, errors, root = export_supplier(client, company, supplier, output, period)
                total_ok += ok
                total_errors += errors
                roots.append(root)

        line("═")
        print(f"Готово. Полностью сохранено: {total_ok}; осталось неполных: {total_errors}.")
        print("Каталоги выгрузки:")
        for root in roots:
            print(f"  {root.resolve()}")
        line("═")
        return 1 if total_errors else 0
    except DokiError as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nОперация остановлена пользователем.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
