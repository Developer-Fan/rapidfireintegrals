# rapidfireintegrals
An experimental website for integral training.

## Run
```bash
python -m pip install -r requirements.txt
python app.py
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
- Separate local integration solver page at `/solver`
- "Give Up" reveals the stored solution
- Daily Easy/Medium/Hard/Newton integrals, archived in `data/daily_integrals.json`
- Expanded integral pools with substantially more per-level variety
- Daily API endpoint:
  - `GET /api/daily/<easy|medium|hard|newton>`
  - Response: `{"integral": "{mathjax integral}", "solution": "{mathjax answer}"}`
