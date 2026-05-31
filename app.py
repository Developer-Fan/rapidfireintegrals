from __future__ import annotations

import json
import random
import threading
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import requests
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "rapidfireintegrals-secret"
socketio = SocketIO(app, async_mode="threading")

NEWTON_API = "https://newton.vercel.app/api/v2"
DATA_FILE = Path(__file__).parent / "data" / "daily_integrals.json"
SUPPORTED_LEVELS = {"easy", "medium", "hard", "newton"}

session_state: dict[str, dict[str, str]] = {}
state_lock = threading.Lock()


def _newton_call(endpoint: str, expression: str) -> str | None:
    try:
        url = f"{NEWTON_API}/{endpoint}/{quote_plus(expression)}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json().get("result")
    except (requests.RequestException, ValueError):
        return None


def _clean_expression(expression: str) -> str:
    return expression.replace("$", "").replace("+ C", "").replace("+C", "").strip()


def _normalize_newton_expression(expression: str) -> str:
    return "".join(expression.split()).replace("(", "").replace(")", "")


def check_answer_with_newton(user_answer: str, expected_integrand: str) -> bool:
    cleaned = _clean_expression(user_answer)
    if not cleaned:
        return False

    derived = _newton_call("derive", cleaned)
    if not derived:
        return False

    simplified_expected = _newton_call("simplify", expected_integrand)
    simplified_derived = _newton_call("simplify", derived)

    if not simplified_expected or not simplified_derived:
        return False

    return _normalize_newton_expression(simplified_expected) == _normalize_newton_expression(
        simplified_derived
    )


def _render_integral_mathjax(integrand_latex: str) -> str:
    return f"\\int {integrand_latex}\\,dx"


def _easy_pool() -> list[dict[str, str]]:
    a = random.randint(1, 6)
    b = random.randint(1, 5)
    n = random.randint(1, 3)
    return [
        {
            "expression": f"{a}*x*({b}+x^2)^{n}",
            "integral": _render_integral_mathjax(f"{a}x({b}+x^2)^{{{n}}}"),
        },
        {
            "expression": f"({a}*x)/(1+x^2)",
            "integral": _render_integral_mathjax(fr"\\frac{{{a}x}}{{1+x^2}}"),
        },
    ]


def _medium_pool() -> list[dict[str, str]]:
    a = random.randint(1, 4)
    b = random.randint(1, 4)
    return [
        {
            "expression": f"{a}*x*sin({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x\\sin({b}x)"),
        },
        {
            "expression": f"{a}*x*cos({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x\\cos({b}x)"),
        },
    ]


def _hard_pool() -> list[dict[str, str]]:
    a = random.randint(1, 3)
    b = random.randint(1, 4)
    return [
        {
            "expression": f"{a}*x^2*e^({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x^2e^{{{b}x}}"),
        },
        {
            "expression": f"{a}*x^2*sin({b}*x)",
            "integral": _render_integral_mathjax(f"{a}x^2\\sin({b}x)"),
        },
    ]


def _newton_pool() -> list[dict[str, str]]:
    a = random.randint(1, 4)
    return [
        {
            "expression": f"{a}*x*ln(x)",
            "integral": _render_integral_mathjax(f"{a}x\\ln(x)"),
        },
        {
            "expression": f"({a}*x)/(1+x^4)",
            "integral": _render_integral_mathjax(fr"\\frac{{{a}x}}{{1+x^4}}"),
        },
    ]


def generate_integral(level: str) -> dict[str, str]:
    level = level.lower()
    pools = {
        "easy": _easy_pool,
        "medium": _medium_pool,
        "hard": _hard_pool,
        "newton": _newton_pool,
    }
    if level not in pools:
        level = "easy"
    challenge = random.choice(pools[level]())
    solution = _newton_call("integrate", challenge["expression"]) or "Unavailable"
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

        random.seed(f"{level}:{key}")
        challenge = generate_integral(level)
        random.seed()

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


@app.route("/api/daily/<level>")
def daily_integral_api(level: str):
    challenge = get_daily_integral(level)
    return jsonify({"integral": challenge["integral"], "solution": challenge["solution"]})


@socketio.on("request_integral")
def handle_request_integral(payload: dict | None = None):
    payload = payload or {}
    level = payload.get("level", "easy")
    challenge = generate_integral(level)

    session_state[request.sid] = challenge
    emit("integral", {"integral": challenge["integral"], "level": level})


@socketio.on("request_daily")
def handle_request_daily(payload: dict | None = None):
    payload = payload or {}
    level = payload.get("level", "easy")
    challenge = get_daily_integral(level)

    session_state[request.sid] = challenge
    emit("integral", {"integral": challenge["integral"], "level": f"daily-{level}"})


@socketio.on("submit_answer")
def handle_submit_answer(payload: dict | None = None):
    payload = payload or {}
    answer = payload.get("answer", "")
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
    challenge = session_state.get(request.sid)
    if not challenge:
        emit("result", {"correct": False, "message": "No active integral."})
        return

    emit(
        "result",
        {
            "correct": False,
            "message": f"Solution: {challenge['solution']} + C",
        },
    )


@socketio.on("disconnect")
def handle_disconnect():
    session_state.pop(request.sid, None)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
