import unittest
from datetime import date

from doki_exporter.console import parse_date, parse_number_selection


class ConsoleTests(unittest.TestCase):
    def test_parse_russian_date(self):
        self.assertEqual(parse_date("31.12.2025"), date(2025, 12, 31))

    def test_parse_iso_date(self):
        self.assertEqual(parse_date("2024-01-01"), date(2024, 1, 1))

    def test_multiple_selection(self):
        self.assertEqual(parse_number_selection("1,3,5-7", 8), [1, 3, 5, 6, 7])

    def test_invalid_selection(self):
        self.assertIsNone(parse_number_selection("7-3", 8))


if __name__ == "__main__":
    unittest.main()
