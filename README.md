# rapidfireintegrals
An experimental website for integral training.

## Run
```bash
python -m pip install -r requirements.txt
python app.py
```

Open http://localhost:5000.

## Features
- Flask + Socket.IO rapid-fire integrals (Easy / Medium / Hard / Newton)
- Integrals rendered with MathJax
- Answers verified server-side with SymPy differentiation and simplification
- Separate local integration solver page at `/solver`
- "Give Up" reveals the stored solution
- Daily Easy/Medium/Hard/Newton integrals, archived in `data/daily_integrals.json`
- Daily API endpoint:
  - `GET /api/daily/<easy|medium|hard|newton>`
  - Response: `{"integral": "{mathjax integral}", "solution": "{mathjax answer}"}`
