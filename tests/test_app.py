import json
import random
import tempfile
import unittest
from datetime import date
from pathlib import Path

import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        app.rate_limit_state.clear()
        app.session_state.clear()

    def test_integral_pools_have_more_variety(self):
        easy_pool = app._easy_pool(app.random.Random(1))
        medium_pool = app._medium_pool(app.random.Random(2))
        hard_pool = app._hard_pool(app.random.Random(3))
        newton_pool = app._newton_pool(app.random.Random(4))

        self.assertGreaterEqual(len(easy_pool), 13)
        self.assertGreaterEqual(len(medium_pool), 14)
        self.assertGreaterEqual(len(hard_pool), 15)
        self.assertGreaterEqual(len(newton_pool), 14)

        easy_expressions = " ".join(item["expression"] for item in easy_pool)
        medium_expressions = " ".join(item["expression"] for item in medium_pool)
        hard_expressions = " ".join(item["expression"] for item in hard_pool)
        newton_expressions = " ".join(item["expression"] for item in newton_pool)

        self.assertIn("ln(", easy_expressions)
        self.assertIn("arctan(", easy_expressions)
        self.assertIn("asinh(", medium_expressions)
        self.assertIn("atanh(", medium_expressions)
        self.assertIn("arcsin(", hard_expressions)
        self.assertIn("ln(1+x^2)", hard_expressions)
        self.assertIn("arctan(", newton_expressions)
        self.assertIn("ln(x)/x", newton_expressions)

    def test_check_answer_uses_sympy(self):
        self.assertTrue(app.check_answer_with_newton("x^2", "2*x"))
        self.assertTrue(app.check_answer_with_newton(r"\frac{x^2}{2}", "x"))
        self.assertTrue(app.check_answer_with_newton(r"3\ln(x^2+1)+C", "6*x/(1+x^2)"))
        self.assertTrue(app.check_answer_with_newton(r"e^{kx}/k", r"e^{kx}"))
        self.assertTrue(app.check_answer_with_newton(r"\sin{kx}", r"k*cos(kx)"))
        self.assertTrue(app.check_answer_with_newton(r"\cos{kx}", r"-k*sin(kx)"))
        self.assertTrue(app.check_answer_with_newton(r"\tan{x}", r"sec(x)^2"))
        self.assertTrue(app.check_answer_with_newton(r"\sech{x}", r"-sech(x)*tanh(x)"))
        self.assertTrue(app.check_answer_with_newton(r"\sqrt{x}", "1/(2*sqrt(x))"))
        self.assertTrue(app.check_answer_with_newton(r"\frac{1}{\sqrt{x}}", "-1/(2*x^(3/2))"))

        self.assertTrue(app.check_answer_with_newton(r"x + c", "1"))
        self.assertTrue(app.check_answer_with_newton(r"x +  C", "1"))
        self.assertTrue(app.check_answer_with_newton(r"x - c", "1"))

    def test_table_driven_answer_equivalences(self):
        positives = []
        negatives = []

        for k in range(1, 13):
            positives.extend(
                [
                    (f"sin({k}*x)/{k}", f"cos({k}*x)"),
                    (rf"\sin{{{k}x}}/{k}", f"cos({k}*x)"),
                    (f"-cos({k}*x)/{k}", f"sin({k}*x)"),
                    (rf"-\cos{{{k}x}}/{k}", f"sin({k}*x)"),
                    (f"e^({k}*x)/{k}", f"e^({k}*x)"),
                    (rf"e^{{{k}x}}/{k}", f"e^({k}*x)"),
                    (f"tan({k}*x)/{k}", f"sec({k}*x)^2"),
                    (f"-cot({k}*x)/{k}", f"csc({k}*x)^2"),
                    (f"tanh({k}*x)/{k}", f"sech({k}*x)^2"),
                    (f"-coth({k}*x)/{k}", f"csch({k}*x)^2"),
                ]
            )
            negatives.extend(
                [
                    (f"sin({k}*x)/{k}", f"sin({k}*x)"),
                    (f"-cos({k}*x)/{k}", f"cos({k}*x)"),
                    (f"e^({k}*x)/{k}", f"sin({k}*x)"),
                    (f"tan({k}*x)/{k}", f"sec({k}*x)"),
                ]
            )

        for answer, integrand in positives:
            with self.subTest(answer=answer, integrand=integrand):
                self.assertTrue(app.check_answer_with_newton(answer, integrand))

        for answer, integrand in negatives:
            with self.subTest(answer=answer, integrand=integrand):
                self.assertFalse(app.check_answer_with_newton(answer, integrand))

    def test_fuzz_normalized_forms(self):
        rng = random.Random(42)
        for _ in range(120):
            k = rng.randint(1, 9)
            family = rng.choice(["sin", "cos", "exp", "sech2", "csc2", "abs"])

            if family == "sin":
                answer = rng.choice([f"-cos({k}*x)/{k}", rf"-\cos{{{k}x}}/{k}"])
                integrand = rng.choice([f"sin({k}*x)", rf"\sin{{{k}x}}"])
            elif family == "cos":
                answer = rng.choice([f"sin({k}*x)/{k}", rf"\sin{{{k}x}}/{k}"])
                integrand = rng.choice([f"cos({k}*x)", rf"\cos{{{k}x}}"])
            elif family == "exp":
                answer = rng.choice([f"e^({k}*x)/{k}", rf"e^{{{k}x}}/{k}", f"exp({k}*x)/{k}"])
                integrand = rng.choice([f"e^({k}*x)", rf"e^{{{k}x}}", f"exp({k}*x)"])
            elif family == "sech2":
                answer = f"tanh({k}*x)/{k}"
                integrand = rng.choice([f"sech({k}*x)^2", rf"\sech^{{2}}{{{k}x}}"])
            elif family == "csc2":
                answer = f"-cot({k}*x)/{k}"
                integrand = rng.choice([f"csc({k}*x)^2", rf"\csc^{{2}}{{{k}x}}"])
            else:
                answer = rng.choice(["x*|x|/2", "x*Abs(x)/2"])
                integrand = rng.choice(["|x|", "Abs(x)"])

            with self.subTest(answer=answer, integrand=integrand):
                self.assertTrue(app.check_answer_with_newton(answer, integrand))

    def test_inverse_and_log_aliases(self):
        self.assertTrue(app.check_answer_with_newton("sin^-1(x)", "1/sqrt(1-x^2)"))
        self.assertTrue(app.check_answer_with_newton(r"\sin^{-1}{x}", "1/sqrt(1-x^2)"))
        self.assertTrue(app.check_answer_with_newton("cos^-1(x)", "-1/sqrt(1-x^2)"))
        self.assertTrue(app.check_answer_with_newton("log10(x)", "1/(x*log(10))"))
        self.assertTrue(app.check_answer_with_newton("log_2(x)", "1/(x*log(2))"))

    def test_generate_integral_uses_sympy(self):
        challenge = app.generate_integral("easy", rng=app.random.Random(1))

        self.assertIn("expression", challenge)
        self.assertIn("solution", challenge)
        self.assertNotEqual(challenge["solution"], "Unavailable")

    def test_linear_combination_builder_caps_at_three_terms(self):
        rng = app.random.Random(11)
        pool = app._medium_pool(app.random.Random(2))
        challenge = app._build_linear_combination_challenge(pool, rng, [3])

        expression = challenge["expression"]
        self.assertEqual(expression.count(" + "), 2)
        self.assertIn("integral", challenge)

    def test_generate_integral_produces_multi_term_challenges(self):
        saw_multi_term = False
        for seed in range(1, 22):
            challenge = app.generate_integral("hard", rng=app.random.Random(seed))
            if " + " in challenge["expression"]:
                saw_multi_term = True
                break

        self.assertTrue(saw_multi_term)

    def test_solver_page_is_available(self):
        response = self.client.get("/solver")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Integration solver", response.data)

    def test_worded_page_is_available(self):
        response = self.client.get("/worded")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Worded Problems", response.data)
        self.assertIn(b"Experimental mode", response.data)

    def test_documentation_page_is_available(self):
        response = self.client.get("/documentation")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"API Documentation", response.data)
        self.assertIn(b"Experimental Worded Flow", response.data)

    def test_privacy_page_is_available(self):
        response = self.client.get("/privacy")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Privacy Policy", response.data)
        self.assertIn(b"local storage", response.data)

    def test_word_problem_bank_has_variety(self):
        bank = app._load_word_problem_bank()
        categories = app._word_problem_categories()

        self.assertGreaterEqual(len(bank.get("names", [])), 8)
        self.assertTrue({"story", "volume", "area", "piecewise"}.issubset(set(categories.keys())))

        for category_name, category_bank in categories.items():
            with self.subTest(category=category_name):
                self.assertGreaterEqual(len(category_bank.get("beginnings", [])), 3)
                self.assertGreaterEqual(len(category_bank.get("middles", [])), 4)
                self.assertGreaterEqual(len(category_bank.get("ends", [])), 2)

        all_middle_integrands = [
            middle.get("integrand", "")
            for category_bank in categories.values()
            for middle in category_bank.get("middles", [])
        ]
        combined_middle_integrands = " ".join(all_middle_integrands)
        self.assertIn("arctan", combined_middle_integrands)
        self.assertIn("asinh", combined_middle_integrands)
        self.assertIn("atanh", combined_middle_integrands)

    def test_word_problem_generation_mentions_category(self):
        challenge = app.generate_word_problem(rng=app.random.Random(7))

        self.assertIn(challenge["category"], {"story", "volume", "area", "piecewise"})
        self.assertIn("prompt", challenge)
        self.assertIn("solution", challenge)

    def test_solver_api_uses_sympy(self):
        response = self.client.post(
            "/api/solve",
            json={"expression": "x^2", "variable": "x"},
        )
        payload = json.loads(response.data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["solution"], "x**3/3")
        self.assertIn("latex", payload)

    def test_solver_api_rejects_oversized_input(self):
        response = self.client.post(
            "/api/solve",
            json={"expression": "x" * (app.MAX_EXPRESSION_LENGTH + 10), "variable": "x"},
        )

        self.assertEqual(response.status_code, 400)

    def test_word_problem_api_flow(self):
        create_response = self.client.post("/api/word-problem")
        create_payload = json.loads(create_response.data)

        self.assertEqual(create_response.status_code, 200)
        self.assertIn("prompt", create_payload)
        self.assertIn("category", create_payload)
        self.assertIn("target_latex", create_payload)
        self.assertTrue(create_payload.get("experimental"))

        check_response = self.client.post("/api/word-problem/check", json={"answer": "0"})
        check_payload = json.loads(check_response.data)
        self.assertEqual(check_response.status_code, 200)
        self.assertFalse(check_payload["correct"])

        reveal_response = self.client.post("/api/word-problem/reveal")
        reveal_payload = json.loads(reveal_response.data)
        self.assertEqual(reveal_response.status_code, 200)
        self.assertIn("solution", reveal_payload)
        self.assertIn("latex", reveal_payload)

        solved_check = self.client.post(
            "/api/word-problem/check",
            json={"answer": reveal_payload["solution"]},
        )
        solved_payload = json.loads(solved_check.data)
        self.assertEqual(solved_check.status_code, 200)
        self.assertTrue(solved_payload["correct"])

    def test_solver_api_rate_limit(self):
        original_limit = app.RATE_LIMIT_MAX_EVENTS_PER_WINDOW
        app.RATE_LIMIT_MAX_EVENTS_PER_WINDOW = 1
        app.rate_limit_state.clear()
        try:
            first = self.client.post("/api/solve", json={"expression": "x", "variable": "x"})
            second = self.client.post("/api/solve", json={"expression": "x", "variable": "x"})
        finally:
            app.RATE_LIMIT_MAX_EVENTS_PER_WINDOW = original_limit
            app.rate_limit_state.clear()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_socket_smoke_flow(self):
        socket_client = app.socketio.test_client(app.app, flask_test_client=self.client)
        self.assertIsNotNone(socket_client)

        socket_client.emit("request_integral", {"level": "easy"})
        first_events = socket_client.get_received()
        integral_events = [event for event in first_events if event["name"] == "integral"]
        self.assertTrue(integral_events)

        socket_client.emit("submit_answer", {"answer": "0"})
        second_events = socket_client.get_received()
        result_events = [event for event in second_events if event["name"] == "result"]
        self.assertTrue(result_events)
        self.assertFalse(result_events[-1]["args"][0]["correct"])

        socket_client.emit("give_up")
        third_events = socket_client.get_received()
        give_up_events = [event for event in third_events if event["name"] == "result"]
        self.assertTrue(give_up_events)
        self.assertTrue(give_up_events[-1]["args"][0].get("is_solution"))
        self.assertIn("solution_latex", give_up_events[-1]["args"][0])

        socket_client.disconnect()

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
