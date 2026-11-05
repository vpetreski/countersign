"""ASGI entry point (uvicorn main:app locally; the Vercel Python runtime imports `app` from here)."""

from countersign.api.app import create_app

app = create_app()
