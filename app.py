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
from flask import Flask, jsonify, render_template, request
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
SUPPORTED_LEVELS = {"easy", "medium", "hard", "newton"}
SESSION_TTL_SECONDS = 3600
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
    "Abs": sp.Abs,
    "root": sp.root,
}

session_state: dict[str, dict[str, str]] = {}
state_lock = threading.Lock()


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
    function_pattern = (
        r"arcsin|arccos|arctan|asinh|acosh|atanh|"
        r"sinh|cosh|tanh|sech|csch|coth|"
        r"sin|cos|tan|sec|csc|cot|ln|log|exp|sqrt"
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
    prepared = _prepare_for_sympy(expression)
    if not prepared:
        return None

    try:
        return parse_expr(
            prepared,
            local_dict=SYMPY_LOCAL_DICT,
            transformations=SYMPY_TRANSFORMATIONS,
            evaluate=True,
        )
    except (SyntaxError, ValueError, TypeError):
        return None


def _expressions_equivalent(left: str, right: str) -> bool:
    left_expr = _parse_expression(left)
    right_expr = _parse_expression(right)
    if left_expr is None or right_expr is None:
        return False

    try:
        return sp.simplify(left_expr - right_expr) == 0
    except Exception:
        return False


def _integrate_expression(expression: str, variable_name: str = "x") -> str:
    parsed = _parse_expression(expression)
    if parsed is None:
        return "Unavailable"

    if not re.fullmatch(r"[A-Za-z]\w*", variable_name):
        return "Unavailable"

    variable = sp.Symbol(variable_name)
    try:
        solution = sp.integrate(parsed, variable)
    except Exception:
        return "Unavailable"

    if getattr(solution, "has", None) and solution.has(sp.Integral):
        return "Unavailable"

    try:
        return sp.sstr(sp.simplify(solution))
    except Exception:
        return sp.sstr(solution)


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
    try:
        derived = sp.diff(answer_expr, x)
    except Exception:
        return False

    try:
        return sp.simplify(derived - expected_expr) == 0
    except Exception:
        return False


def _render_integral_mathjax(integrand_latex: str) -> str:
    return rf"\displaystyle \int {integrand_latex}\,dx"


def _latex_fraction(numerator: str, denominator: str) -> str:
    return rf"\frac{{{numerator}}}{{{denominator}}}"


def _easy_pool(rng: random.Random) -> list[dict[str, str]]:
    a = rng.randint(1, 6)
    b = rng.randint(2, 6)
    c = rng.randint(1, 5)
    n = rng.randint(2, 5)
    return [
        {
            "expression": f"{a}*x^{n}",
            "integral": _render_integral_mathjax(f"{a}x^{{{n}}}"),
        },
        {
            "expression": f"{a}*e^({b}*x)",
            "integral": _render_integral_mathjax(f"{a}e^{{{b}x}}"),
        },
        {
            "expression": f"{a}*sin({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\sin({b}x)"),
        },
        {
            "expression": f"{a}*cos({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\cos({b}x)"),
        },
        {
            "expression": f"{a}*x/({b}+x^2)",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}x", f"{b}+x^2")),
        },
        {
            "expression": f"{a}/({b}+x)",
            "integral": _render_integral_mathjax(_latex_fraction(str(a), f"{b}+x")),
        },
        {
            "expression": f"{a}/sqrt(x)",
            "integral": _render_integral_mathjax(_latex_fraction(str(a), "\\sqrt{x}")),
        },
        {
            "expression": f"{a}*sec({c}*x)^2",
            "integral": _render_integral_mathjax(f"{a}\\sec^2({c}x)"),
        },
        {
            "expression": f"{a}*csc({c}*x)^2",
            "integral": _render_integral_mathjax(f"{a}\\csc^2({c}x)"),
        },
        {
            "expression": f"{a}*(x+{c})^{n}",
            "integral": _render_integral_mathjax(f"{a}(x+{c})^{{{n}}}"),
        },
        {
            "expression": f"{a}*sqrt(x+{c})",
            "integral": _render_integral_mathjax(f"{a}\\sqrt{{x+{c}}}"),
        },
    ]


def _medium_pool(rng: random.Random) -> list[dict[str, str]]:
    a = rng.randint(1, 4)
    b = rng.randint(1, 5)
    c = rng.randint(2, 5)
    d = rng.randint(1, 4)
    return [
        {
            "expression": f"{a}*x*sin({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x\\sin({b}x)"),
        },
        {
            "expression": f"{a}*x*cos({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x\\cos({b}x)"),
        },
        {
            "expression": f"{a}*x*e^({b}*x)",
            "integral": _render_integral_mathjax(f"{a}xe^{{{b}x}}"),
        },
        {
            "expression": f"{a}/(1+x^2)",
            "integral": _render_integral_mathjax(_latex_fraction(str(a), "1+x^2")),
        },
        {
            "expression": f"{a}/sqrt(1-x^2)",
            "integral": _render_integral_mathjax(f"{a}\\arcsin(x)"),
        },
        {
            "expression": f"{a}*x/sqrt({c}+x^2)",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}x", f"\\sqrt{{{c}+x^2}}")),
        },
        {
            "expression": f"{a}*sec({b}*x)*tan({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\sec({b}x)\\tan({b}x)"),
        },
        {
            "expression": f"{a}*csc({b}*x)*cot({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\csc({b}x)\\cot({b}x)"),
        },
        {
            "expression": f"{a}*x/(1+x^{d})",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}x", f"1+x^{d}")),
        },
        {
            "expression": f"{a}*exp({b}*x)/(1+exp({b}*x))",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}e^{{{b}x}}", f"1+e^{{{b}x}}")),
        },
        {
            "expression": f"{a}*sinh({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\sinh({b}x)"),
        },
        {
            "expression": f"{a}*cosh({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\cosh({b}x)"),
        },
    ]


def _hard_pool(rng: random.Random) -> list[dict[str, str]]:
    a = rng.randint(1, 3)
    b = rng.randint(1, 4)
    c = rng.randint(1, 4)
    d = rng.randint(2, 5)
    return [
        {
            "expression": f"{a}*x^2*e^({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x^2e^{{{b}x}}"),
        },
        {
            "expression": f"{a}*x^2*sin({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x^2\\sin({b}x)"),
        },
        {
            "expression": f"{a}*x^2*cos({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x^2\\cos({b}x)"),
        },
        {
            "expression": f"{a}*ln(x)",
            "integral": _render_integral_mathjax(f"{a}\\ln(x)"),
        },
        {
            "expression": f"{a}*x*ln(x)",
            "integral": _render_integral_mathjax(f"{a}x\\ln(x)"),
        },
        {
            "expression": f"{a}*e^({b}*x)*sin({c}*x)",
            "integral": _render_integral_mathjax(f"{a}e^{{{b}x}}\\sin({c}x)"),
        },
        {
            "expression": f"{a}*e^({b}*x)*cos({c}*x)",
            "integral": _render_integral_mathjax(f"{a}e^{{{b}x}}\\cos({c}x)"),
        },
        {
            "expression": f"{a}*x^3*sin({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x^3\\sin({b}x)"),
        },
        {
            "expression": f"{a}*x^3*cos({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x^3\\cos({b}x)"),
        },
        {
            "expression": f"{a}*x^2/(1+x^{d})",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}x^2", f"1+x^{d}")),
        },
        {
            "expression": f"{a}*sech({b}*x)^2",
            "integral": _render_integral_mathjax(f"{a}\\operatorname{{sech}}^2({b}x)"),
        },
        {
            "expression": f"{a}*csch({b}*x)^2",
            "integral": _render_integral_mathjax(f"{a}\\operatorname{{csch}}^2({b}x)"),
        },
        {
            "expression": f"{a}*tanh({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\tanh({b}x)"),
        },
    ]


def _newton_pool(rng: random.Random) -> list[dict[str, str]]:
    a = rng.randint(1, 4)
    b = rng.randint(1, 4)
    c = rng.randint(2, 5)
    d = rng.randint(2, 5)
    return [
        {
            "expression": f"{a}/x",
            "integral": _render_integral_mathjax(f"{a}/x"),
        },
        {
            "expression": f"{a}/(x*ln(x))",
            "integral": _render_integral_mathjax(f"{a}/(x\\ln(x))"),
        },
        {
            "expression": f"{a}*x/(1+x^2)",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}x", "1+x^2")),
        },
        {
            "expression": f"{a}*x/sqrt({b}^2-x^2)",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}x", f"\\sqrt{{{b}^2-x^2}}")),
        },
        {
            "expression": f"{a}/(1+({b}*x)^2)",
            "integral": _render_integral_mathjax(f"{a}/(1+({b}x)^2)"),
        },
        {
            "expression": f"{a}*e^({b}*x)*cos({c}*x)",
            "integral": _render_integral_mathjax(f"{a}e^{{{b}x}}\\cos({c}x)"),
        },
        {
            "expression": f"{a}/(x^2+{d}^2)",
            "integral": _render_integral_mathjax(_latex_fraction(str(a), f"x^2+{d}^2")),
        },
        {
            "expression": f"{a}/sqrt(x^2+{d})",
            "integral": _render_integral_mathjax(_latex_fraction(str(a), f"\\sqrt{{x^2+{d}}}")),
        },
        {
            "expression": f"{a}*sech({b}*x)*tanh({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\operatorname{{sech}}({b}x)\\tanh({b}x)"),
        },
        {
            "expression": f"{a}*csch({b}*x)*coth({b}*x)",
            "integral": _render_integral_mathjax(f"{a}\\operatorname{{csch}}({b}x)\\coth({b}x)"),
        },
        {
            "expression": f"{a}*exp({b}*x)/(1+exp({b}*x)^2)",
            "integral": _render_integral_mathjax(_latex_fraction(f"{a}e^{{{b}x}}", f"1+e^{{2{b}x}}")),
        },
        {
            "expression": f"{a}*(x^2+{d})^{-1}",
            "integral": _render_integral_mathjax(_latex_fraction(str(a), f"x^2+{d}")),
        },
    ]


def generate_integral(level: str, rng: random.Random | None = None) -> dict[str, str]:
    level = level.lower()
    rng = rng or random.Random()
    pools = {
        "easy": _easy_pool,
        "medium": _medium_pool,
        "hard": _hard_pool,
        "newton": _newton_pool,
    }
    if level not in pools:
        level = "easy"
    challenge = rng.choice(pools[level](rng))
    solution = _integrate_expression(challenge["expression"], "x")
    challenge["solution"] = solution
    return challenge


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


@app.post("/api/solve")
def solve_api():
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


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
