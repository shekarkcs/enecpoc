# Validation in the generation environment

- Python 3.12: `python -m unittest discover -s tests -v` completed with **12 tests passed**.
- `python -m compileall -q backend/app` passed.
- Frontend dependencies installed and `package-lock.json` generated.
- `npm run typecheck` passed.
- `npm run build` completed successfully (Next.js 15.5.28).
- A temporary Uvicorn process served `/health` and an authenticated `/batches` HTTP request successfully; it was stopped afterward.

Tests use SQLite, mock SAP and mocked model responses. Entra tenant login, live model calls, real SAP, browser interactions, Azure deployment and production security controls have **not** been validated. See implementation.md for the explicit production gap list. No cloud deployment was performed.
