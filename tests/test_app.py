import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    @patch("app.requests.get")
    def test_check_answer_uses_newton(self, mock_get):
        def fake_get(url, timeout):
            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    if "/derive/" in url:
                        return {"result": "2*x"}
                    if "/simplify/" in url and "2%2Ax" in url:
                        return {"result": "2*x"}
                    if "/simplify/" in url:
                        return {"result": "2*x"}
                    return {"result": ""}

            return Response()

        mock_get.side_effect = fake_get
        self.assertTrue(app.check_answer_with_newton("x^2", "2*x"))

    @patch("app.requests.get")
    def test_daily_api_returns_integral_and_solution(self, mock_get):
        with tempfile.TemporaryDirectory() as tmp:
            app.DATA_FILE = Path(tmp) / "daily_integrals.json"

            def fake_get(url, timeout):
                class Response:
                    def raise_for_status(self):
                        return None

                    def json(self):
                        if "/integrate/" in url:
                            return {"result": "x^2/2"}
                        return {"result": "x"}

                return Response()

            mock_get.side_effect = fake_get
            response = self.client.get("/api/daily/easy")
            payload = json.loads(response.data)

            self.assertEqual(response.status_code, 200)
            self.assertIn("integral", payload)
            self.assertIn("solution", payload)
            self.assertTrue(app.DATA_FILE.exists())

    @patch("app.requests.get")
    def test_daily_integral_is_archived(self, mock_get):
        with tempfile.TemporaryDirectory() as tmp:
            app.DATA_FILE = Path(tmp) / "daily_integrals.json"

            def fake_get(url, timeout):
                class Response:
                    def raise_for_status(self):
                        return None

                    def json(self):
                        if "/integrate/" in url:
                            return {"result": "cached"}
                        return {"result": "x"}

                return Response()

            mock_get.side_effect = fake_get

            first = app.get_daily_integral("medium", day=date(2026, 5, 31))
            second = app.get_daily_integral("medium", day=date(2026, 5, 31))

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
