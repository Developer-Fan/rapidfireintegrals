import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    def test_integral_pools_have_more_variety(self):
        self.assertGreaterEqual(len(app._easy_pool(app.random.Random(1))), 5)
        self.assertGreaterEqual(len(app._medium_pool(app.random.Random(2))), 6)
        self.assertGreaterEqual(len(app._hard_pool(app.random.Random(3))), 6)
        self.assertGreaterEqual(len(app._newton_pool(app.random.Random(4))), 6)

    def test_check_answer_uses_sympy(self):
        self.assertTrue(app.check_answer_with_newton("x^2", "2*x"))
        self.assertTrue(app.check_answer_with_newton(r"\frac{x^2}{2}", "x"))
        self.assertTrue(app.check_answer_with_newton(r"3\ln(x^2+1)+C", "6*x/(1+x^2)"))
        self.assertTrue(app.check_answer_with_newton(r"e^{kx}/k", r"e^{kx}"))
        self.assertTrue(app.check_answer_with_newton(r"\sqrt{x}", "1/(2*sqrt(x))"))
        self.assertTrue(app.check_answer_with_newton(r"\frac{1}{\sqrt{x}}", "-1/(2*x^(3/2))"))

        self.assertTrue(app.check_answer_with_newton(r"x + c", "1"))
        self.assertTrue(app.check_answer_with_newton(r"x +  C", "1"))
        self.assertTrue(app.check_answer_with_newton(r"x - c", "1"))

    def test_generate_integral_uses_sympy(self):
        challenge = app.generate_integral("easy", rng=app.random.Random(1))

        self.assertIn("expression", challenge)
        self.assertIn("solution", challenge)
        self.assertNotEqual(challenge["solution"], "Unavailable")

    def test_solver_page_is_available(self):
        response = self.client.get("/solver")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Integration solver", response.data)

    def test_solver_api_uses_sympy(self):
        response = self.client.post(
            "/api/solve",
            json={"expression": "x^2", "variable": "x"},
        )
        payload = json.loads(response.data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["solution"], "x**3/3")
        self.assertIn("latex", payload)

    def test_daily_api_returns_integral_and_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            app.DATA_FILE = Path(tmp) / "daily_integrals.json"
            response = self.client.get("/api/daily/easy")
            payload = json.loads(response.data)

            self.assertEqual(response.status_code, 200)
            self.assertIn("integral", payload)
            self.assertIn("solution", payload)
            self.assertTrue(app.DATA_FILE.exists())

    def test_daily_integral_is_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            app.DATA_FILE = Path(tmp) / "daily_integrals.json"

            first = app.get_daily_integral("medium", day=date(2026, 5, 31))
            second = app.get_daily_integral("medium", day=date(2026, 5, 31))

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
