# rapidfireintegrals
An experimental website for integral training.

## Run
```bash
python -m pip install -r requirements.txt
python app.py
```

## Render Deployment
The repository includes a `render.yaml` for Render Web Services. It installs the Python dependencies and
starts the app with Gunicorn + eventlet so Socket.IO upgrades work in production.

If you deploy manually, use:

```bash
RAPIDFIRE_ASYNC_MODE=eventlet gunicorn -k eventlet -w 1 -b 0.0.0.0:$PORT app:app
```

Socket.IO defaults to `threading` mode for stable local development.
To opt into eventlet explicitly:

```bash
RAPIDFIRE_ASYNC_MODE=eventlet python app.py
```

Open http://localhost:5000.

## Features
- Flask + Socket.IO rapid-fire integrals (Easy / Medium / Hard / Newton)
- Integrals rendered with MathJax
- Answers verified server-side with SymPy differentiation and simplification
- Accepts common LaTeX-style function forms like `e^{kx}`, `\sin{kx}`, `\cos{x}`, `\tan{x}`, and `\sech{x}`
- Supports additional parser aliases like `sin^-1(x)`, `log10(x)`, `log_2(x)`, `sin^2(x)`, and absolute values `|x|`
- Separate local integration solver page at `/solver`
- Experimental worded-problem page at `/worded` (explicitly marked beta on the page)
- API documentation page at `/documentation` with matching markdown in `documentation/api.md`
- Privacy policy page at `/privacy`
- Q streak, daily streak, and points on the main page
- Streak/points and theme preference are stored in browser local storage (no account required)
- Worded problems are mix-and-match combinations of beginnings, middles, and ends from `data/word_problems.json`
- "Give Up" reveals the stored solution
- Daily Easy/Medium/Hard/Newton integrals, archived in `data/daily_integrals.json`
- Core integral prompt templates are data-driven in `data/integral_templates.json`
- Expanded integral pools with substantially more per-level variety
- Includes launch guardrails: expression length limits, symbolic complexity caps, operation-time budgets, and per-client rate limiting
- Daily API endpoint:
  - `GET /api/daily/<easy|medium|hard|newton>`
  - Response: `{"integral": "{mathjax integral}", "solution": "{mathjax answer}"}`
