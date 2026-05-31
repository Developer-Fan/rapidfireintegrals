from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import date
from pathlib import Path

eventlet = None
SOCKETIO_ASYNC_MODE = os.environ.get("RAPIDFIRE_ASYNC_MODE", "threading").strip().lower()
if SOCKETIO_ASYNC_MODE == "eventlet":
    try:
        import eventlet as _eventlet

        _eventlet.monkey_patch()
        eventlet = _eventlet
    except Exception:
        SOCKETIO_ASYNC_MODE = "threading"

import sympy as sp
from flask import Flask, jsonify, render_template, request, session
from flask_socketio import SocketIO, emit
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("RAPIDFIRE_SECRET_KEY", "dev-secret-change-me")
# Default to threading for stable dev behavior; opt into eventlet via RAPIDFIRE_ASYNC_MODE=eventlet.
socketio = SocketIO(app, async_mode=SOCKETIO_ASYNC_MODE, cors_allowed_origins="*")

DATA_FILE = Path(__file__).parent / "data" / "daily_integrals.json"
WORD_PROBLEM_FILE = Path(__file__).parent / "data" / "word_problems.json"
INTEGRAL_TEMPLATE_FILE = Path(__file__).parent / "data" / "integral_templates.json"
SUPPORTED_LEVELS = {"easy", "medium", "hard", "newton"}
SESSION_TTL_SECONDS = 3600
MAX_EXPRESSION_LENGTH = int(os.environ.get("RAPIDFIRE_MAX_EXPRESSION_LENGTH", "280"))
MAX_SYMBOLIC_OPS = int(os.environ.get("RAPIDFIRE_MAX_SYMBOLIC_OPS", "280"))
PARSE_BUDGET_SECONDS = float(os.environ.get("RAPIDFIRE_PARSE_BUDGET_SECONDS", "0.10"))
SIMPLIFY_BUDGET_SECONDS = float(os.environ.get("RAPIDFIRE_SIMPLIFY_BUDGET_SECONDS", "0.20"))
INTEGRATE_BUDGET_SECONDS = float(os.environ.get("RAPIDFIRE_INTEGRATE_BUDGET_SECONDS", "0.45"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RAPIDFIRE_RATE_LIMIT_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_EVENTS_PER_WINDOW = int(os.environ.get("RAPIDFIRE_RATE_LIMIT_MAX_EVENTS", "45"))
SYMPY_TRANSFORMATIONS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)
SYMPY_LOCAL_DICT = {
    "E": sp.E,
    "I": sp.I,
    "pi": sp.pi,
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "sec": sp.sec,
    "csc": sp.csc,
    "cot": sp.cot,
    "ln": sp.log,
    "log": sp.log,
    "log10": lambda value: sp.log(value, 10),
    "exp": sp.exp,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "sech": sp.sech,
    "csch": sp.csch,
    "coth": sp.coth,
    "sqrt": sp.sqrt,
    "asinh": sp.asinh,
    "acosh": sp.acosh,
    "atanh": sp.atanh,
    "arcsin": sp.asin,
    "arccos": sp.acos,
    "arctan": sp.atan,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "Abs": sp.Abs,
    "Piecewise": sp.Piecewise,
    "root": sp.root,
}

session_state: dict[str, dict[str, str]] = {}
rate_limit_state: dict[str, list[float]] = {}
state_lock = threading.Lock()
word_problem_bank_cache: dict | None = None
integral_template_cache: dict[str, list[dict[str, str]]] | None = None


def _within_budget(started_at: float, budget_seconds: float) -> bool:
    return (time.perf_counter() - started_at) <= budget_seconds


def _is_rate_limited(key: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with state_lock:
        events = rate_limit_state.setdefault(key, [])
        events[:] = [stamp for stamp in events if stamp >= cutoff]
        if len(events) >= RATE_LIMIT_MAX_EVENTS_PER_WINDOW:
            return True
        events.append(now)
        return False


def _socket_rate_limited() -> bool:
    sid_key = f"sid:{request.sid}"
    ip_key = f"ip:{request.remote_addr or 'unknown'}"
    return _is_rate_limited(sid_key) or _is_rate_limited(ip_key)


def _normalize_abs_bars(expression: str) -> str:
    normalized = expression
    for _ in range(6):
        updated = re.sub(r"\|([^|]+)\|", r"Abs(\1)", normalized)
        if updated == normalized:
            break
        normalized = updated
    return normalized


def _normalize_inverse_aliases(expression: str) -> str:
    normalized = expression
    alias_map = {
        "sin": "arcsin",
        "cos": "arccos",
        "tan": "arctan",
        "sinh": "asinh",
        "cosh": "acosh",
        "tanh": "atanh",
    }
    for base, inverse in alias_map.items():
        normalized = re.sub(rf"\b{base}\s*\^\s*-\s*1\s*\(([^()]+)\)", rf"{inverse}(\1)", normalized)
        normalized = re.sub(rf"\b{base}\s*\^\s*-\s*1\s*\{{([^{{}}]+)\}}", rf"{inverse}(\1)", normalized)
        normalized = re.sub(rf"\b{base}\s*\^\s*\{{\s*-\s*1\s*\}}\s*\(([^()]+)\)", rf"{inverse}(\1)", normalized)
        normalized = re.sub(rf"\b{base}\s*\^\s*\{{\s*-\s*1\s*\}}\s*\{{([^{{}}]+)\}}", rf"{inverse}(\1)", normalized)
    return normalized


def _clean_expression(expression: str) -> str:
    expression = expression.strip()
    expression = expression.replace("$", "")
    expression = expression.replace("\\left", "").replace("\\right", "")
    expression = expression.replace("\\,", "").replace("\\!", "")
    expression = expression.replace("\\cdot", "*").replace("\\times", "*")
    expression = expression.replace("\\div", "/")
    expression = expression.replace("−", "-")
    expression = re.sub(r"\s*([+\-])\s*[Cc]\s*$", "", expression)
    expression = re.sub(
        r"\\(arcsin|arccos|arctan|asinh|acosh|atanh|sinh|cosh|tanh|sech|csch|coth|sin|cos|tan|sec|csc|cot|ln|log|exp|sqrt)",
        r"\1",
        expression,
    )
    expression = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"(\1)/(\2)", expression)
    expression = re.sub(r"^\\\((.*)\\\)$", r"\1", expression)
    expression = re.sub(r"^\\\[(.*)\\\]$", r"\1", expression)
    expression = re.sub(r"(?<=\d)(?=[A-Za-z(])", "*", expression)
    expression = re.sub(r"(?<=[A-Za-z\)])(?=\d)", "*", expression)
    expression = re.sub(r"(?<=[xX)])(?=\()", "*", expression)
    expression = re.sub(r"(?<=\d)(?=\()", "*", expression)
    expression = re.sub(r"(?<=\))(?=[A-Za-z(])", "*", expression)
    return expression.strip()


def _prepare_for_sympy(expression: str) -> str:
    cleaned = _clean_expression(expression)
    cleaned = _normalize_abs_bars(cleaned)
    cleaned = _normalize_inverse_aliases(cleaned)

    cleaned = re.sub(r"\blog10\s*\(([^()]+)\)", r"log(\1, 10)", cleaned)
    cleaned = re.sub(r"\blog10\s*\{([^{}]+)\}", r"log(\1, 10)", cleaned)
    cleaned = re.sub(r"\blog_\{([^{}]+)\}\s*\(([^()]+)\)", r"log(\2, \1)", cleaned)
    cleaned = re.sub(r"\blog_\{([^{}]+)\}\s*\{([^{}]+)\}", r"log(\2, \1)", cleaned)
    cleaned = re.sub(r"\blog_([A-Za-z0-9.]+)\s*\(([^()]+)\)", r"log(\2, \1)", cleaned)
    cleaned = re.sub(r"\blog_([A-Za-z0-9.]+)\s*\{([^{}]+)\}", r"log(\2, \1)", cleaned)

    function_pattern = (
        r"arcsin|arccos|arctan|asinh|acosh|atanh|"
        r"sinh|cosh|tanh|sech|csch|coth|"
        r"sin|cos|tan|sec|csc|cot|ln|log|exp|sqrt"
    )
    cleaned = re.sub(
        rf"\b({function_pattern})\s*\^\s*([-+]?\d+)\s*\(([^()]+)\)",
        r"(\1(\3))**(\2)",
        cleaned,
    )
    cleaned = re.sub(
        rf"\b({function_pattern})\s*\^\s*([-+]?\d+)\s*\{{([^{{}}]+)\}}",
        r"(\1(\3))**(\2)",
        cleaned,
    )
    cleaned = re.sub(
        rf"\b({function_pattern})\s*\^\s*\{{([^{{}}]+)\}}\s*\(([^()]+)\)",
        r"(\1(\3))**(\2)",
        cleaned,
    )
    cleaned = re.sub(
        rf"\b({function_pattern})\s*\^\s*\{{([^{{}}]+)\}}\s*\{{([^{{}}]+)\}}",
        r"(\1(\3))**(\2)",
        cleaned,
    )
    cleaned = re.sub(
        rf"\b({function_pattern})\s*\{{([^{{}}]+)\}}",
        r"\1(\2)",
        cleaned,
    )
    cleaned = re.sub(r"(?<![A-Za-z0-9_])e\^\{([^{}]+)\}", r"E**(\1)", cleaned)
    cleaned = re.sub(r"(?<![A-Za-z0-9_])e\^\(([^()]+)\)", r"E**(\1)", cleaned)
    cleaned = re.sub(r"(?<![A-Za-z0-9_])e\^([A-Za-z0-9_]+)", r"E**(\1)", cleaned)
    cleaned = re.sub(r"sqrt\[(\d+)\]\{([^{}]+)\}", r"root(\2, \1)", cleaned)
    cleaned = re.sub(r"sqrt\s*\{([^{}]+)\}", r"sqrt(\1)", cleaned)
    cleaned = re.sub(r"sqrt\s*\(([^()]+)\)", r"sqrt(\1)", cleaned)
    cleaned = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"(\1)/(\2)", cleaned)
    cleaned = re.sub(r"\\pi\b", "pi", cleaned)
    cleaned = re.sub(r"\be\b", "E", cleaned)
    return cleaned


def _parse_expression(expression: str) -> sp.Expr | None:
    if len(expression.strip()) > MAX_EXPRESSION_LENGTH:
        return None

    prepared = _prepare_for_sympy(expression)
    if not prepared:
        return None
    if len(prepared) > MAX_EXPRESSION_LENGTH:
        return None

    started_at = time.perf_counter()
    try:
        parsed = parse_expr(
            prepared,
            local_dict=SYMPY_LOCAL_DICT,
            transformations=SYMPY_TRANSFORMATIONS,
            evaluate=True,
        )
    except (SyntaxError, ValueError, TypeError):
        return None

    if not _within_budget(started_at, PARSE_BUDGET_SECONDS):
        return None

    try:
        if sp.count_ops(parsed, visual=False) > MAX_SYMBOLIC_OPS:
            return None
    except Exception:
        return None

    return parsed


def _expressions_equivalent(left: str, right: str) -> bool:
    left_expr = _parse_expression(left)
    right_expr = _parse_expression(right)
    if left_expr is None or right_expr is None:
        return False

    simplify_start = time.perf_counter()
    try:
        result = sp.simplify(left_expr - right_expr) == 0
    except Exception:
        return False
    return bool(result and _within_budget(simplify_start, SIMPLIFY_BUDGET_SECONDS))


def _integrate_expression(expression: str, variable_name: str = "x") -> str:
    parsed = _parse_expression(expression)
    if parsed is None:
        return "Unavailable"

    if not re.fullmatch(r"[A-Za-z]\w*", variable_name):
        return "Unavailable"

    variable = sp.Symbol(variable_name)
    integrate_start = time.perf_counter()
    try:
        solution = sp.integrate(parsed, variable)
    except Exception:
        return "Unavailable"

    if not _within_budget(integrate_start, INTEGRATE_BUDGET_SECONDS):
        return "Unavailable"

    if getattr(solution, "has", None) and solution.has(sp.Integral):
        return "Unavailable"

    simplify_start = time.perf_counter()
    try:
        simplified_solution = sp.simplify(solution)
    except Exception:
        return sp.sstr(solution)

    if not _within_budget(simplify_start, SIMPLIFY_BUDGET_SECONDS):
        return "Unavailable"

    return sp.sstr(simplified_solution)


def _expression_to_latex(expression: str) -> str:
    parsed = _parse_expression(expression)
    if parsed is None:
        return expression

    try:
        return sp.latex(parsed)
    except Exception:
        return expression


def _prune_sessions_locked() -> None:
    now = time.time()
    stale_sids = [
        sid
        for sid, data in session_state.items()
        if now - float(data.get("created_at", now)) > SESSION_TTL_SECONDS
    ]
    for sid in stale_sids:
        session_state.pop(sid, None)


def check_answer_with_newton(user_answer: str, expected_integrand: str) -> bool:
    cleaned = _clean_expression(user_answer)
    if not cleaned:
        return False

    answer_expr = _parse_expression(cleaned)
    expected_expr = _parse_expression(expected_integrand)
    if answer_expr is None or expected_expr is None:
        return False

    x = sp.Symbol("x")
    diff_start = time.perf_counter()
    try:
        derived = sp.diff(answer_expr, x)
    except Exception:
        return False

    if not _within_budget(diff_start, SIMPLIFY_BUDGET_SECONDS):
        return False

    simplify_start = time.perf_counter()
    try:
        equivalent = sp.simplify(derived - expected_expr) == 0
    except Exception:
        return False
    return bool(equivalent and _within_budget(simplify_start, SIMPLIFY_BUDGET_SECONDS))


def _render_integral_mathjax(integrand_latex: str) -> str:
    return rf"\displaystyle \int {integrand_latex}\,dx"


def _latex_fraction(numerator: str, denominator: str) -> str:
    return rf"\frac{{{numerator}}}{{{denominator}}}"


def _load_integral_templates() -> dict[str, list[dict[str, str]]]:
    global integral_template_cache
    if integral_template_cache is not None:
        return integral_template_cache

    if not INTEGRAL_TEMPLATE_FILE.exists():
        integral_template_cache = {}
        return integral_template_cache

    try:
        parsed = json.loads(INTEGRAL_TEMPLATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        integral_template_cache = {}
        return integral_template_cache

    templates: dict[str, list[dict[str, str]]] = {}
    for level, entries in parsed.items():
        if not isinstance(entries, list):
            continue
        cleaned_entries = [entry for entry in entries if isinstance(entry, dict)]
        templates[str(level)] = cleaned_entries

    integral_template_cache = templates
    return integral_template_cache


def _render_integral_pool(level: str, values: dict[str, int]) -> list[dict[str, str]]:
    templates = _load_integral_templates().get(level, [])
    rendered: list[dict[str, str]] = []
    for entry in templates:
        expression_template = str(entry.get("expression", "x"))
        integral_template = str(entry.get("integral_latex", "x"))
        try:
            expression = expression_template.format(**values)
            integral_latex = integral_template.format(**values)
        except (KeyError, ValueError):
            continue
        rendered.append(
            {
                "expression": expression,
                "integral": _render_integral_mathjax(integral_latex),
            }
        )

    if rendered:
        return rendered

    # Fallback keeps the app running if the template file is missing or malformed.
    return [{"expression": "x", "integral": _render_integral_mathjax("x")}]


def _easy_pool(rng: random.Random) -> list[dict[str, str]]:
    values = {
        "a": rng.randint(1, 6),
        "b": rng.randint(2, 6),
        "c": rng.randint(1, 5),
        "d": rng.randint(1, 4),
        "n": rng.randint(2, 5),
    }
    return _render_integral_pool("easy", values)


def _medium_pool(rng: random.Random) -> list[dict[str, str]]:
    values = {
        "a": rng.randint(1, 4),
        "b": rng.randint(1, 5),
        "c": rng.randint(2, 5),
        "d": rng.randint(1, 4),
        "n": rng.randint(2, 5),
    }
    return _render_integral_pool("medium", values)


def _hard_pool(rng: random.Random) -> list[dict[str, str]]:
    values = {
        "a": rng.randint(1, 3),
        "b": rng.randint(1, 4),
        "c": rng.randint(1, 4),
        "d": rng.randint(2, 5),
        "n": rng.randint(2, 5),
    }
    return _render_integral_pool("hard", values)


def _newton_pool(rng: random.Random) -> list[dict[str, str]]:
    values = {
        "a": rng.randint(1, 4),
        "b": rng.randint(1, 4),
        "c": rng.randint(2, 5),
        "d": rng.randint(2, 5),
        "n": rng.randint(2, 5),
    }
    return _render_integral_pool("newton", values)


def _load_word_problem_bank() -> dict:
    global word_problem_bank_cache
    if word_problem_bank_cache is not None:
        return word_problem_bank_cache

    if not WORD_PROBLEM_FILE.exists():
        word_problem_bank_cache = {"names": ["Bob", "Jane"], "categories": {}}
        return word_problem_bank_cache

    try:
        word_problem_bank_cache = json.loads(WORD_PROBLEM_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        word_problem_bank_cache = {"names": ["Bob", "Jane"], "categories": {}}
    return word_problem_bank_cache


def _word_problem_values(rng: random.Random, bank: dict) -> dict[str, int | str]:
    names = bank.get("names") or ["Bob", "Jane", "Kai", "Mina", "Ari", "Noah"]
    return {
        "name": rng.choice(names),
        "a": rng.randint(2, 8),
        "b": rng.randint(1, 6),
        "c": rng.randint(1, 5),
        "d": rng.randint(2, 7),
        "m": rng.randint(1, 4),
        "n": rng.randint(2, 5),
        "p": rng.randint(1, 4),
        "q": rng.randint(1, 5),
        "r": rng.randint(2, 7),
        "lower": rng.randint(0, 3),
        "upper": rng.randint(4, 9),
        "pi": "pi",
    }


def _format_word_problem_text(text: str, values: dict[str, int | str]) -> str:
    return str(text).format(**values).strip()


def _render_word_problem_entry(
    category: str,
    beginning: dict,
    middle: dict,
    end: dict,
    values: dict[str, int | str],
) -> dict[str, str]:
    beginning_text = _format_word_problem_text(beginning.get("text", ""), values)
    middle_text = _format_word_problem_text(middle.get("text", ""), values)
    end_text = _format_word_problem_text(end.get("text", ""), values)
    prompt = " ".join(part for part in [beginning_text, middle_text, end_text] if part)
    integrand = _format_word_problem_text(middle.get("integrand", ""), values)
    return {
        "category": category,
        "prompt": prompt,
        "integrand": integrand,
    }


def _word_problem_categories() -> dict[str, dict]:
    bank = _load_word_problem_bank()
    categories = bank.get("categories", {})
    if isinstance(categories, dict) and categories:
        return {name: entry for name, entry in categories.items() if isinstance(entry, dict)}

    entries = bank.get("entries", [])
    if isinstance(entries, list) and entries:
        grouped: dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            category = str(entry.get("category", "story"))
            grouped.setdefault(category, {"beginnings": [], "middles": [], "ends": []})
            grouped[category]["beginnings"].append({"text": entry.get("beginning", "")})
            grouped[category]["middles"].append(
                {"text": entry.get("middle", ""), "integrand": entry.get("integrand", "")}
            )
            grouped[category]["ends"].append({"text": entry.get("end", "")})
        return grouped

    return {}


def generate_word_problem(rng: random.Random | None = None) -> dict[str, str]:
    rng = rng or random.Random()
    bank = _load_word_problem_bank()
    categories = _word_problem_categories()
    if not categories:
        challenge = {
            "category": "story",
            "prompt": "A fallback problem is unavailable.",
            "integrand": "x",
        }
    else:
        category_name = rng.choice(sorted(categories.keys()))
        category_bank = categories[category_name]
        beginnings = category_bank.get("beginnings", [])
        middles = category_bank.get("middles", [])
        ends = category_bank.get("ends", [])
        if not beginnings or not middles or not ends:
            challenge = {
                "category": category_name,
                "prompt": "A fallback problem is unavailable.",
                "integrand": "x",
            }
        else:
            challenge = _render_word_problem_entry(
                category_name,
                rng.choice(beginnings),
                rng.choice(middles),
                rng.choice(ends),
                _word_problem_values(rng, bank),
            )
    challenge["solution"] = _integrate_expression(challenge["integrand"], "x")
    return challenge


def _build_linear_combination_challenge(
    pool: list[dict[str, str]],
    rng: random.Random,
    term_options: list[int] | None = None,
) -> dict[str, str]:
    if not pool:
        return {
            "expression": "x",
            "integral": _render_integral_mathjax("x"),
        }

    options = [count for count in (term_options or [1, 2, 2, 3]) if count >= 1]
    term_count = min(rng.choice(options), len(pool))
    terms = rng.sample(pool, k=term_count)

    if term_count == 1:
        return dict(terms[0])

    expression = " + ".join(f"({term['expression']})" for term in terms)
    return {
        "expression": expression,
        "integral": _render_integral_mathjax(_expression_to_latex(expression)),
    }


def generate_integral(level: str, rng: random.Random | None = None) -> dict[str, str]:
    level = level.lower()
    rng = rng or random.Random()
    pools = {
        "easy": _easy_pool,
        "medium": _medium_pool,
        "hard": _hard_pool,
        "newton": _newton_pool,
    }
    term_options_by_level = {
        "easy": [1, 2, 2, 3],
        "medium": [1, 2, 2, 3],
        "hard": [2, 2, 3],
        "newton": [2, 2, 3],
    }
    if level not in pools:
        level = "easy"

    pool = pools[level](rng)
    for _ in range(7):
        challenge = _build_linear_combination_challenge(pool, rng, term_options_by_level[level])
        solution = _integrate_expression(challenge["expression"], "x")
        if solution != "Unavailable":
            challenge["solution"] = solution
            return challenge

    fallback = dict(rng.choice(pool))
    fallback["solution"] = _integrate_expression(fallback["expression"], "x")
    return fallback


def _load_daily_data() -> dict:
    if not DATA_FILE.exists():
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps({}), encoding="utf-8")
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_daily_data(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def get_daily_integral(level: str, day: date | None = None) -> dict[str, str]:
    level = level.lower()
    if level not in SUPPORTED_LEVELS:
        level = "easy"

    day = day or date.today()
    key = day.isoformat()

    with state_lock:
        data = _load_daily_data()
        level_data = data.setdefault(level, {})
        if key in level_data:
            return level_data[key]

        challenge = generate_integral(level, rng=random.Random(f"{level}:{key}"))

        level_data[key] = {
            "expression": challenge["expression"],
            "integral": challenge["integral"],
            "solution": challenge["solution"],
        }
        _save_daily_data(data)
        return level_data[key]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/solver")
def solver():
    return render_template("solver.html")


@app.route("/worded")
def worded():
    return render_template("worded.html")


@app.route("/documentation")
def documentation():
    return render_template("documentation.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.post("/api/word-problem")
def word_problem_api():
    if _is_rate_limited(f"word-problem:{request.remote_addr or 'unknown'}"):
        return jsonify({"error": "Rate limit exceeded. Please slow down."}), 429

    challenge = generate_word_problem()
    session["word_problem_integrand"] = challenge["integrand"]
    session["word_problem_solution"] = challenge["solution"]

    return jsonify(
        {
            "prompt": challenge["prompt"],
            "category": challenge["category"],
            "target_latex": _expression_to_latex(challenge["integrand"]),
            "experimental": True,
        }
    )


@app.post("/api/word-problem/check")
def word_problem_check_api():
    if _is_rate_limited(f"word-problem-check:{request.remote_addr or 'unknown'}"):
        return jsonify({"correct": False, "message": "Rate limit exceeded. Please slow down."}), 429

    payload = request.get_json(silent=True) or request.form or {}
    answer = str(payload.get("answer", ""))
    integrand = str(session.get("word_problem_integrand", ""))
    if not integrand:
        return jsonify({"correct": False, "message": "Request a worded problem first."}), 400

    is_correct = check_answer_with_newton(answer, integrand)
    if is_correct:
        return jsonify({"correct": True, "message": "Correct. Nice modeling."})
    return jsonify({"correct": False, "message": "Not quite. Revise and try again."})


@app.post("/api/word-problem/reveal")
def word_problem_reveal_api():
    if _is_rate_limited(f"word-problem-reveal:{request.remote_addr or 'unknown'}"):
        return jsonify({"error": "Rate limit exceeded. Please slow down."}), 429

    integrand = str(session.get("word_problem_integrand", ""))
    if not integrand:
        return jsonify({"error": "Request a worded problem first."}), 400

    solution = str(session.get("word_problem_solution") or _integrate_expression(integrand, "x"))
    return jsonify({"solution": solution, "latex": _expression_to_latex(solution)})


@app.post("/api/solve")
def solve_api():
    if _is_rate_limited(f"solve:{request.remote_addr or 'unknown'}"):
        return jsonify({"solution": "Unavailable", "error": "Rate limit exceeded. Please slow down."}), 429

    payload = request.get_json(silent=True) or request.form or {}
    expression = str(payload.get("expression", ""))
    variable = str(payload.get("variable", "x")).strip() or "x"
    solution = _integrate_expression(expression, variable)
    if solution == "Unavailable":
        return jsonify({"solution": solution}), 400
    return jsonify({"solution": solution, "latex": _expression_to_latex(solution)})


@app.route("/api/daily/<level>")
def daily_integral_api(level: str):
    challenge = get_daily_integral(level)
    return jsonify({"integral": challenge["integral"], "solution": challenge["solution"]})


@socketio.on("request_integral")
def handle_request_integral(payload: dict | None = None):
    if _socket_rate_limited():
        emit("result", {"correct": False, "message": "Rate limit exceeded. Please slow down."})
        return

    payload = payload or {}
    level = payload.get("level", "easy")
    challenge = generate_integral(level)

    with state_lock:
        _prune_sessions_locked()
        challenge["created_at"] = str(time.time())
        session_state[request.sid] = challenge
    emit("integral", {"integral": challenge["integral"], "level": level})


@socketio.on("request_daily")
def handle_request_daily(payload: dict | None = None):
    if _socket_rate_limited():
        emit("result", {"correct": False, "message": "Rate limit exceeded. Please slow down."})
        return

    payload = payload or {}
    level = payload.get("level", "easy")
    challenge = get_daily_integral(level)

    with state_lock:
        _prune_sessions_locked()
        challenge["created_at"] = str(time.time())
        session_state[request.sid] = challenge
    emit("integral", {"integral": challenge["integral"], "level": f"daily-{level}"})


@socketio.on("submit_answer")
def handle_submit_answer(payload: dict | None = None):
    if _socket_rate_limited():
        emit("result", {"correct": False, "message": "Rate limit exceeded. Please slow down."})
        return

    payload = payload or {}
    answer = payload.get("answer", "")
    with state_lock:
        _prune_sessions_locked()
        challenge = session_state.get(request.sid)

    if not challenge:
        emit(
            "result",
            {
                "correct": False,
                "message": "Request an integral first.",
            },
        )
        return

    is_correct = check_answer_with_newton(answer, challenge["expression"])
    if is_correct:
        emit("result", {"correct": True, "message": "Well done!"})
    else:
        emit(
            "result",
            {
                "correct": False,
                "message": "Not quite. Try again or click Give Up.",
            },
        )


@socketio.on("give_up")
def handle_give_up():
    if _socket_rate_limited():
        emit("result", {"correct": False, "message": "Rate limit exceeded. Please slow down."})
        return

    with state_lock:
        _prune_sessions_locked()
        challenge = session_state.get(request.sid)
    if not challenge:
        emit("result", {"correct": False, "message": "No active integral."})
        return

    emit(
        "result",
        {
            "correct": False,
            "message": f"Solution: {challenge['solution']} + C",
            "is_solution": True,
            "solution_latex": _expression_to_latex(challenge["solution"]),
        },
    )


@socketio.on("disconnect")
def handle_disconnect():
    with state_lock:
        session_state.pop(request.sid, None)
        rate_limit_state.pop(f"sid:{request.sid}", None)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
