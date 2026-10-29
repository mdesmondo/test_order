# test_calculator.py
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from order_calculator import (
    Product,
    calculate_order_for_product,
    calculate_orders,
    format_report,
    get_fallback_products,
    load_products_from_csv,
    main,
)


class TestOrderCalculationFormula(unittest.TestCase):
    """Тесты основной формулы расчета и краевых случаев"""

    def test_standard_order_calculation(self) -> None:
        """Обычный расчет заказа (часть на складе, часть в пути)"""
        product = Product(
            sku="A003",
            name="Sugar",
            stock=8,
            daily_sales=2.0,
            incoming=5,
            lead_days=7,
            safety_stock=6,
        )
        result = calculate_order_for_product(product)

        # проверяем каждый шаг формулы по очереди
        self.assertEqual(result.coverage_period, 14)  # 7 + 7 дней
        self.assertEqual(result.target_stock, 34)  # ceil(2 * 14 + 6)
        self.assertEqual(result.available_stock, 13)  # 8 на складе + 5 в пути
        self.assertEqual(result.order_qty, 21)  # 34 - 13
        self.assertTrue(result.needs_order)

    def test_no_order_needed_when_stock_exceeds_target(self) -> None:
        """Заказ не требуется, если на складе и так большой остаток"""
        product = Product(
            sku="A004",
            name="Cookies",
            stock=100,
            daily_sales=5.0,
            incoming=0,
            lead_days=3,
            safety_stock=15,
        )
        result = calculate_order_for_product(product)

        self.assertEqual(result.coverage_period, 10)
        self.assertEqual(result.target_stock, 65)
        self.assertEqual(result.available_stock, 100)
        self.assertEqual(result.order_qty, 0)
        self.assertFalse(result.needs_order)

    def test_no_order_needed_when_incoming_covers_deficit(self) -> None:
        """Товар в пути полностью перекрывает потребность (второй заказ не нужен)"""
        product = Product(
            sku="A008",
            name="Oatmeal",
            stock=5,
            daily_sales=1.3,
            incoming=35,
            lead_days=6,
            safety_stock=4,
        )
        result = calculate_order_for_product(product)

        # период 13 дней, целевой запас 1.3 * 13 + 4 = 20.9 -> округляем до 21
        self.assertEqual(result.coverage_period, 13)
        self.assertEqual(result.target_stock, 21)
        self.assertEqual(result.available_stock, 40)  # 5 + 35 в пути
        self.assertEqual(result.order_qty, 0)
        self.assertFalse(result.needs_order)

    def test_exact_match_available_equals_target(self) -> None:
        """Доступное количество ровно совпадает с целевым запасом"""
        product = Product(
            sku="EX01",
            name="ExactItem",
            stock=15,
            daily_sales=2.0,
            incoming=5,
            lead_days=3,
            safety_stock=0,
        )
        result = calculate_order_for_product(product)

        self.assertEqual(result.target_stock, 20)
        self.assertEqual(result.available_stock, 20)
        self.assertEqual(result.order_qty, 0)

    def test_zero_daily_sales_with_zero_safety_stock(self) -> None:
        """Нулевые продажи и нулевой страховой запас (заказываем 0)"""
        product = Product(
            sku="A005",
            name="Water",
            stock=0,
            daily_sales=0.0,
            incoming=0,
            lead_days=2,
            safety_stock=0,
        )
        result = calculate_order_for_product(product)

        self.assertEqual(result.coverage_period, 9)
        self.assertEqual(result.target_stock, 0)
        self.assertEqual(result.order_qty, 0)

    def test_zero_daily_sales_with_safety_stock_deficit(self) -> None:
        """Продажи равны нулю, но нужно добрать до страхового запаса"""
        product = Product(
            sku="A007",
            name="Honey",
            stock=3,
            daily_sales=0.0,
            incoming=0,
            lead_days=5,
            safety_stock=10,
        )
        result = calculate_order_for_product(product)

        self.assertEqual(result.target_stock, 10)
        self.assertEqual(result.available_stock, 3)
        self.assertEqual(result.order_qty, 7)

    def test_fractional_daily_sales_ceil_rounding(self) -> None:
        """Проверка округления вверх math.ceil при дробных продажах"""
        product = Product(
            sku="A009",
            name="Olive Oil",
            stock=4,
            daily_sales=0.75,
            incoming=2,
            lead_days=4,
            safety_stock=5,
        )
        result = calculate_order_for_product(product)

        # 0.75 * 11 + 5 = 13.25 -> округляется вверх до 14
        self.assertEqual(result.coverage_period, 11)
        self.assertAlmostEqual(result.raw_target_stock, 13.25)
        self.assertEqual(result.target_stock, 14)
        self.assertEqual(result.available_stock, 6)
        self.assertEqual(result.order_qty, 8)

    def test_floating_point_precision_does_not_over_round(self) -> None:
        """Защита от мусора float (1.15 * 20 не должно превращаться в 24)"""
        product = Product(
            sku="FP01",
            name="FloatEdge",
            stock=0,
            daily_sales=1.15,
            incoming=0,
            lead_days=13,  # период покрытия = 13 + 7 = 20
            safety_stock=0,
        )
        result = calculate_order_for_product(product)
        self.assertEqual(result.target_stock, 23)
        self.assertEqual(result.order_qty, 23)


class TestProductValidation(unittest.TestCase):
    """Проверка валидации полей в классе Product"""

    def test_negative_values_raise_value_error(self) -> None:
        """проверка: отрицательные числа не должны проходить"""
        base_kwargs = {
            "sku": "V001",
            "name": "Test",
            "stock": 10,
            "daily_sales": 1.0,
            "incoming": 0,
            "lead_days": 3,
            "safety_stock": 2,
        }
        # по очереди подставляем -1 в каждое числовое поле
        for field in ("stock", "daily_sales", "incoming", "lead_days", "safety_stock"):
            with self.subTest(field=field):
                invalid_kwargs = dict(base_kwargs)
                invalid_kwargs[field] = -1
                with self.assertRaises(ValueError):
                    Product(**invalid_kwargs)  # type: ignore[arg-type]

    def test_empty_sku_or_name_raises_value_error(self) -> None:
        """пустые артикул или название вызывают ошибку"""
        with self.assertRaises(ValueError):
            Product("   ", "Valid Name", 1, 1.0, 0, 1, 1)
        with self.assertRaises(ValueError):
            Product("SKU1", "", 1, 1.0, 0, 1, 1)

    def test_invalid_types_raise_type_error(self) -> None:
        """неверные типы данных (float вместо int, bool, строки) отсекаются"""
        with self.assertRaises(TypeError):
            Product("SKU1", "Item", 1.5, 1.0, 0, 1, 1)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Product("SKU1", "Item", True, 1.0, 0, 1, 1)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Product("SKU1", "Item", 1, "2.5", 0, 1, 1)  # type: ignore[arg-type]


class TestCsvLoaderAndCli(unittest.TestCase):
    """Тесты загрузки из csv и запуска через консоль"""

    def test_load_valid_csv_file(self) -> None:
        """чтение корректного csv файла во временной папке"""
        csv_content = (
            "sku,name,stock,daily_sales,incoming,lead_days,safety_stock\n"
            "A001,Coffee,12,4,0,5,8\n"
            "A006,Milk,0,1.5,0,1,2\n"
        )
        # создаем временный файл для проверки чтения
        with tempfile.NamedTemporaryFile("w", suffix=".csv", encoding="utf-8", delete=False) as tmp:
            tmp.write(csv_content)
            tmp_path = Path(tmp.name)

        try:
            products = load_products_from_csv(tmp_path)
            self.assertEqual(len(products), 2)
            self.assertEqual(products[0].sku, "A001")
            self.assertEqual(products[1].daily_sales, 1.5)
        finally:
            # удаляем временный файл после теста
            tmp_path.unlink(missing_ok=True)

    def test_duplicate_sku_in_csv_raises_value_error(self) -> None:
        """проверка на дубликаты артикулов в файле"""
        csv_content = (
            "sku,name,stock,daily_sales,incoming,lead_days,safety_stock\n"
            "A001,Coffee,12,4,0,5,8\n"
            "A001,Coffee Clone,5,1,0,2,1\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".csv", encoding="utf-8", delete=False) as tmp:
            tmp.write(csv_content)
            tmp_path = Path(tmp.name)

        try:
            with self.assertRaises(ValueError):
                load_products_from_csv(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_missing_columns_in_csv_raises_value_error(self) -> None:
        """ошибка структуры файла, если не хватает нужных колонок"""
        csv_content = "sku,name,stock,daily_sales\nA001,Coffee,12,4\n"
        with tempfile.NamedTemporaryFile("w", suffix=".csv", encoding="utf-8", delete=False) as tmp:
            tmp.write(csv_content)
            tmp_path = Path(tmp.name)

        try:
            with self.assertRaises(ValueError):
                load_products_from_csv(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_fallback_dataset_and_report_formatting(self) -> None:
        """проверка встроенного списка и формирования текстового отчета"""
        products = get_fallback_products()
        self.assertGreaterEqual(len(products), 6)
        calcs = calculate_orders(products)
        report = format_report(calcs, source_label="test")
        self.assertIn("СВОДНАЯ ТАБЛИЦА ТОВАРОВ", report)
        self.assertIn("ПОЗИЦИИ, ТРЕБУЮЩИЕ ЗАКАЗА", report)
        self.assertIn("ПОЗИЦИИ, НЕ ТРЕБУЮЩИЕ ЗАКАЗА", report)

    def test_cli_main_returns_zero_on_valid_file(self) -> None:
        """успешный запуск main со стандартным csv файлом"""
        default_csv = Path(__file__).resolve().parent / "products.csv"
        buf = io.StringIO()
        # перехватываем вывод в консоль, чтобы не засорять лог тестов
        with contextlib.redirect_stdout(buf):
            exit_code = main(["--file", str(default_csv)])
        self.assertEqual(exit_code, 0)
        self.assertIn("ОТЧЁТ ПО РЕКОМЕНДУЕМЫМ ЗАКАЗАМ ПОСТАВЩИКАМ", buf.getvalue())

    def test_cli_main_returns_one_on_missing_file(self) -> None:
        """возврат кода ошибки 1, если указанный файл не найден"""
        err_buf = io.StringIO()
        with contextlib.redirect_stderr(err_buf):
            exit_code = main(["--file", "non_existent_file_12345.csv"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Ошибка:", err_buf.getvalue())

if __name__ == "__main__":
    unittest.main()