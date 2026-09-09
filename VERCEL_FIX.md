# TANMAY NEWS API — Vercel Internal Server Error Fix

This build fixes the Vercel runtime/import and template-bundling setup.

- `api/index.py` explicitly adds the project root before importing `app.py`.
- `vercel.json` uses the current `functions` configuration.
- `templates/**` is explicitly bundled into the Python function.
- Python is pinned to 3.12.
- No `.env` file.
- No `os.getenv()` / `os.environ.get()` configuration.

After deploying, test `/`, `/api`, `/api/health`, `/user`, and `/admin`.
