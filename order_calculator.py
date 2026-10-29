# order_calculator.py
from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

EXTRA_COVERAGE_DAYS: int = 7
DEFAULT_CSV_FILENAME: str = "products.csv"


@dataclass(frozen=True, slots=True)
class Product:
    """Модель товара со складскими показателями"""

    sku: str
    name: str
    stock: int
    daily_sales: float
    incoming: int
    lead_days: int
    safety_stock: int

    def __post_init__(self) -> None:
        """Проверка входных данных при создании товара"""
        # 1. проверяем строковые поля на пустоту
        if not isinstance(self.sku, str) or not self.sku.strip():
            raise ValueError("Поле 'sku' должно быть непустой строкой.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"[{self.sku}] Поле 'name' должно быть непустой строкой.")

        int_fields = {
            "stock": self.stock,
            "incoming": self.incoming,
            "lead_days": self.lead_days,
            "safety_stock": self.safety_stock,
        }
        # 2. проверяем целочисленные показатели
        for field_name, value in int_fields.items():
            # в питоне bool наследуется от int, поэтому отсекаем его отдельно
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(
                    f"[{self.sku}] Поле '{field_name}' должно быть целым числом (int), "
                    f"получено: {type(value).__name__}."
                )
            if value < 0:
                raise ValueError(
                    f"[{self.sku}] Поле '{field_name}' не может быть отрицательным "
                    f"(получено: {value})."
                )

        # 3. проверяем средние продажи в день (могут быть дробными)
        if isinstance(self.daily_sales, bool) or not isinstance(
            self.daily_sales, (int, float)
        ):
            raise TypeError(
                f"[{self.sku}] Поле 'daily_sales' должно быть числом (int или float), "
                f"получено: {type(self.daily_sales).__name__}."
            )
        if math.isnan(self.daily_sales) or math.isinf(self.daily_sales):
            raise ValueError(
                f"[{self.sku}] Поле 'daily_sales' должно быть конечным числом."
            )
        if self.daily_sales < 0:
            raise ValueError(
                f"[{self.sku}] Поле 'daily_sales' не может быть отрицательным "
                f"(получено: {self.daily_sales})."
            )

        # убираем лишние пробелы по краям и приводим продажи к float
        object.__setattr__(self, "sku", self.sku.strip())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "daily_sales", float(self.daily_sales))


@dataclass(frozen=True, slots=True)
class OrderCalculation:
    """Результат расчета заказа по конкретному товару"""

    product: Product
    coverage_period: int
    raw_target_stock: float
    target_stock: int
    available_stock: int
    order_qty: int

    @property
    def needs_order(self) -> bool:
        """флаг: нужно ли заказывать товар"""
        return self.order_qty > 0


def calculate_order_for_product(
    product: Product,
    extra_days: int = EXTRA_COVERAGE_DAYS,
) -> OrderCalculation:
    """Расчет рекомендуемого заказа для одного товара (основная формула)"""
    if isinstance(extra_days, bool) or not isinstance(extra_days, int) or extra_days < 0:
        raise ValueError("Параметр 'extra_days' должен быть неотрицательным целым числом.")

    # 1. считаем период покрытия (срок поставки + 7 дней запаса)
    coverage_period = product.lead_days + extra_days

    # 2. считаем целевой запас и убираем технический хвост float перед округлением вверх
    raw_target_stock = round(
        product.daily_sales * coverage_period + product.safety_stock, 9
    )
    target_stock = math.ceil(raw_target_stock)

    # 3. складываем текущий остаток и товар в пути
    available_stock = product.stock + product.incoming

    # 4. определяем итоговое количество к заказу (не меньше нуля)
    order_qty = max(0, target_stock - available_stock)

    return OrderCalculation(
        product=product,
        coverage_period=coverage_period,
        raw_target_stock=raw_target_stock,
        target_stock=target_stock,
        available_stock=available_stock,
        order_qty=order_qty,
    )


def calculate_orders(products: Iterable[Product]) -> list[OrderCalculation]:
    """Прогоняет расчет заказа по всему списку товаров"""
    return [calculate_order_for_product(p) for p in products]


REQUIRED_CSV_COLUMNS: frozenset[str] = frozenset(
    {
        "sku",
        "name",
        "stock",
        "daily_sales",
        "incoming",
        "lead_days",
        "safety_stock",
    }
)


def get_fallback_products() -> list[Product]:
    """Дефолтный список товаров (на случай если csv файла нет)"""
    return [
        Product("A001", "Coffee", stock=12, daily_sales=4.0, incoming=0, lead_days=5, safety_stock=8),
        Product("A002", "Tea", stock=50, daily_sales=3.0, incoming=20, lead_days=4, safety_stock=10),
        Product("A003", "Sugar", stock=8, daily_sales=2.0, incoming=5, lead_days=7, safety_stock=6),
        Product("A004", "Cookies", stock=100, daily_sales=5.0, incoming=0, lead_days=3, safety_stock=15),
        Product("A005", "Water", stock=0, daily_sales=0.0, incoming=0, lead_days=2, safety_stock=0),
        Product("A006", "Milk", stock=0, daily_sales=1.5, incoming=0, lead_days=1, safety_stock=2),
        Product("A007", "Honey", stock=3, daily_sales=0.0, incoming=0, lead_days=5, safety_stock=10),
        Product("A008", "Oatmeal", stock=5, daily_sales=1.3, incoming=35, lead_days=6, safety_stock=4),
        Product("A009", "Olive Oil", stock=4, daily_sales=0.75, incoming=2, lead_days=4, safety_stock=5),
    ]


def _parse_int_field(raw_value: str | None, field_name: str, line_num: int) -> int:
    """перевод значения из csv в int с проверкой на пустоту"""
    if raw_value is None or not raw_value.strip():
        raise ValueError(f"Строка {line_num}: пустое значение в обязательном поле '{field_name}'.")
    try:
        return int(raw_value.strip())
    except ValueError as exc:
        raise ValueError(
            f"Строка {line_num}: поле '{field_name}' должно быть целым числом, "
            f"получено '{raw_value}'."
        ) from exc


def _parse_float_field(raw_value: str | None, field_name: str, line_num: int) -> float:
    """перевод значения из csv в float с проверкой на пустоту"""
    if raw_value is None or not raw_value.strip():
        raise ValueError(f"Строка {line_num}: пустое значение в обязательном поле '{field_name}'.")
    try:
        return float(raw_value.strip())
    except ValueError as exc:
        raise ValueError(
            f"Строка {line_num}: поле '{field_name}' должно быть числом, "
            f"получено '{raw_value}'."
        ) from exc


def load_products_from_csv(file_path: str | Path) -> list[Product]:
    """Безопасное чтение товаров из csv с проверкой структуры и дубликатов"""
    path = Path(file_path)
    # проверяем наличие csv файла
    if not path.is_file():
        raise FileNotFoundError(f"CSV-файл не найден: {path}")

    products: list[Product] = []
    seen_skus: set[str] = set()

    # открываем с utf-8-sig, чтобы не ломалось об BOM из экселя
    with path.open(mode="r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise ValueError(f"Файл '{path}' пуст или не содержит заголовка CSV.")

        # 1. проверяем что все нужные колонки на месте
        normalized_headers = {h.strip() for h in reader.fieldnames if h}
        missing_cols = REQUIRED_CSV_COLUMNS - normalized_headers
        if missing_cols:
            missing_str = ", ".join(sorted(missing_cols))
            raise ValueError(
                f"В файле '{path}' отсутствуют обязательные колонки: {missing_str}."
            )

        # 2. построчно читаем и собираем объекты товаров
        for line_num, row in enumerate(reader, start=2):
            # пропускаем пустые строки, если они есть в конце файла
            if not any(val and val.strip() for val in row.values()):
                continue

            # приводим ключи и значения к чистым строкам без пробелов
            clean_row = {k.strip(): (v.strip() if v is not None else "") for k, v in row.items() if k}
            sku = clean_row.get("sku", "")
            if not sku:
                raise ValueError(f"Строка {line_num}: отсутствует код товара 'sku'.")
            if sku in seen_skus:
                raise ValueError(f"Строка {line_num}: дублирующийся артикул SKU '{sku}'.")

            try:
                product = Product(
                    sku=sku,
                    name=clean_row.get("name", ""),
                    stock=_parse_int_field(clean_row.get("stock"), "stock", line_num),
                    daily_sales=_parse_float_field(clean_row.get("daily_sales"), "daily_sales", line_num),
                    incoming=_parse_int_field(clean_row.get("incoming"), "incoming", line_num),
                    lead_days=_parse_int_field(clean_row.get("lead_days"), "lead_days", line_num),
                    safety_stock=_parse_int_field(clean_row.get("safety_stock"), "safety_stock", line_num),
                )
            # обработка ошибок валидации конкретной строки
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Ошибка валидации в строке {line_num}: {exc}") from exc

            seen_skus.add(product.sku)
            products.append(product)

    if not products:
        raise ValueError(f"Файл '{path}' не содержит ни одной записи о товарах.")

    return products


def load_products(csv_path: str | Path | None = None) -> tuple[list[Product], str]:
    """Универсальная загрузка товаров (из csv или встроенного списка)"""
    # 1. если путь передали явно через флаг, читаем строго его
    if csv_path is not None:
        explicit_path = Path(csv_path)
        return load_products_from_csv(explicit_path), f"CSV-файл ({explicit_path})"

    # 2. проверяем, лежит ли дефолтный products.csv рядом со скриптом
    default_path = Path(__file__).resolve().parent / DEFAULT_CSV_FILENAME
    if default_path.is_file():
        return load_products_from_csv(default_path), f"CSV-файл ({default_path.name})"

    # 3. если файла нет, отдаем встроенный набор данных
    return get_fallback_products(), "встроенный демонстрационный набор (fallback)"


def _format_number(value: float) -> str:
    """убирает лишний .0 у целых чисел при выводе"""
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def format_report(
    calculations: Sequence[OrderCalculation],
    source_label: str = "CSV",
) -> str:
    """Сборка итогового отчета для вывода в консоль"""
    lines: list[str] = []
    sep = "=" * 96
    thin_sep = "-" * 96

    lines.append(sep)
    lines.append("ОТЧЁТ ПО РЕКОМЕНДУЕМЫМ ЗАКАЗАМ ПОСТАВЩИКАМ")
    lines.append(f"Источник данных: {source_label} | Всего позиций обработано: {len(calculations)}")
    lines.append(sep)
    lines.append("")

    # 1. собираем общую сводную таблицу по всем позициям
    lines.append("1. СВОДНАЯ ТАБЛИЦА ТОВАРОВ")
    lines.append(thin_sep)
    header = (
        f"{'SKU':<6} | {'Товар':<12} | {'Остаток':>7} | {'В пути':>6} | "
        f"{'Доступно':>8} | {'Продажи/дн':>10} | {'Покрытие':>8} | "
        f"{'Страх.зап':>9} | {'Целевой':>7} | {'К ЗАКАЗУ':>8}"
    )
    lines.append(header)
    lines.append(thin_sep)

    for calc in calculations:
        p = calc.product
        sales_str = _format_number(p.daily_sales)
        cov_str = f"{calc.coverage_period} дн."
        order_str = f"{calc.order_qty} шт." if calc.needs_order else "0 (ок)"
        row_str = (
            f"{p.sku:<6} | {p.name:<12} | {p.stock:>7} | {p.incoming:>6} | "
            f"{calc.available_stock:>8} | {sales_str:>10} | {cov_str:>8} | "
            f"{p.safety_stock:>9} | {calc.target_stock:>7} | {order_str:>8}"
        )
        lines.append(row_str)

    lines.append(thin_sep)
    lines.append("")

    # разделяем товары на две группы: что нужно заказать и что уже в норме
    to_order = [c for c in calculations if c.needs_order]
    no_order = [c for c in calculations if not c.needs_order]

    # 2. выводим подробный разбор по позициям, которые пора заказывать
    lines.append(f"2. ПОЗИЦИИ, ТРЕБУЮЩИЕ ЗАКАЗА ({len(to_order)} из {len(calculations)})")
    lines.append(thin_sep)
    if not to_order:
        lines.append("Все товары в достаточном количестве. Заказы не требуются.")
    else:
        total_units = sum(c.order_qty for c in to_order)
        for idx, calc in enumerate(to_order, start=1):
            p = calc.product
            sales_str = _format_number(p.daily_sales)
            raw_target_str = _format_number(calc.raw_target_stock)
            lines.append(
                f"{idx}. [{p.sku}] {p.name} — РЕКОМЕНДУЕМЫЙ ЗАКАЗ: {calc.order_qty} шт."
            )
            lines.append(
                f"   • Период покрытия : {p.lead_days} дн. (поставка) + "
                f"{EXTRA_COVERAGE_DAYS} дн. = {calc.coverage_period} дн."
            )
            lines.append(
                f"   • Целевой запас   : ceil({sales_str} шт./дн. × {calc.coverage_period} дн. "
                f"+ {p.safety_stock} шт. страх.) = ceil({raw_target_str}) = {calc.target_stock} шт."
            )
            lines.append(
                f"   • Доступный запас : {p.stock} шт. (склад) + {p.incoming} шт. (в пути) "
                f"= {calc.available_stock} шт."
            )
            lines.append(
                f"   • Расчёт заказа   : max(0, {calc.target_stock} - {calc.available_stock}) "
                f"= {calc.order_qty} шт."
            )
        lines.append(thin_sep)
        lines.append(
            f"ИТОГО к заказу: {len(to_order)} поз., суммарно {total_units} шт."
        )
    lines.append("")

    # 3. выводим список товаров, где запаса достаточно
    lines.append(f"3. ПОЗИЦИИ, НЕ ТРЕБУЮЩИЕ ЗАКАЗА ({len(no_order)} из {len(calculations)})")
    lines.append(thin_sep)
    if not no_order:
        lines.append("Нет позиций (требуется заказ по всем товарам).")
    else:
        for calc in no_order:
            p = calc.product
            surplus = calc.available_stock - calc.target_stock
            lines.append(
                f"• [{p.sku}] {p.name:<10} — заказ 0 шт. "
                f"(доступно: {calc.available_stock} шт. [склад {p.stock} + в пути {p.incoming}], "
                f"целевой запас: {calc.target_stock} шт., запас сверх цели: +{surplus} шт.)"
            )
    lines.append(sep)

    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Настройка аргументов командной строки"""
    parser = argparse.ArgumentParser(
        description="Расчёт рекомендуемого заказа товаров у поставщиков."
    )
    parser.add_argument(
        "-f",
        "--file",
        type=str,
        default=None,
        help=(
            f"Путь к CSV-файлу с данными о товарах "
            f"(по умолчанию ищется '{DEFAULT_CSV_FILENAME}' рядом со скриптом; "
            f"если файл отсутствует — используется встроенный набор данных)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Главный метод, запуск расчета и вывода по очереди"""
    args = parse_args(argv)
    try:
        products, source_label = load_products(args.file)
        calculations = calculate_orders(products)
        report = format_report(calculations, source_label=source_label)
        print(report)
        return 0
    # обработка ошибок при чтении или валидации
    except (FileNotFoundError, ValueError, TypeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())