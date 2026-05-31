# API Documentation

This directory contains the public JSON API reference for Rapid Fire Integrals.
The app is intentionally experimental and the worded-problem flow is explicitly marked as beta.

## Core Endpoints

### `POST /api/solve`
Integrate an expression locally with SymPy.

Request body:
```json
{
  "expression": "x^2",
  "variable": "x"
}
```

Response:
```json
{
  "solution": "x**3/3",
  "latex": "\frac{x^{3}}{3}"
}
```

### `GET /api/daily/<easy|medium|hard|newton>`
Fetch or create the daily integral for a given level.

Response:
```json
{
  "integral": "\\displaystyle \\int x^2\\,dx",
  "solution": "x**3/3"
}
```

## Experimental Worded Flow

### `POST /api/word-problem`
Creates a session-backed word problem.

Response:
```json
{
  "prompt": "...",
  "category": "volume",
  "target_latex": "...",
  "experimental": true
}
```

### `POST /api/word-problem/check`
Checks an answer against the current session problem.

Request body:
```json
{
  "answer": "x^2/2"
}
```

Response:
```json
{
  "correct": true,
  "message": "Correct. Nice modeling."
}
```

### `POST /api/word-problem/reveal`
Reveals the current solution in raw SymPy form and LaTeX form.

Response:
```json
{
  "solution": "x**3/3",
  "latex": "\frac{x^{3}}{3}"
}
```

## Notes

- Responses are JSON unless validation fails or a rate limit is hit.
- The worded flow depends on the current session.
- Rate limits apply to solver and worded endpoints.
- Worded problems are assembled from separate beginning / middle / end banks in `data/word_problems.json`, so each category multiplies into many distinct prompts.
- Main-page progress features (Q streak, daily streak, and points) are client-side only and saved in browser local storage.
- See `/privacy` for the full privacy policy.
