"""
FastAPI application entrypoint for the Music Player backend.

This backend is intentionally PUBLIC (no authentication):
- POST /songs/upload
- GET /songs
- GET /songs/{song_id}/stream

CORS is enabled for local development (http://localhost:3000) and can be extended
via environment variables.
"""

from __future__ import annotations

import os as _os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes_songs import router as songs_router

openapi_tags = [
    {"name": "Songs", "description": "Upload, list, and stream mp3 files (public)."},
    {"name": "Health", "description": "Service health and basic runtime info."},
]

app = FastAPI(
    title="Music Player Backend API",
    description=(
        "Backend for a personal music library.\n\n"
        "Authentication: none (public API)\n\n"
        "Streaming:\n"
        "- GET /songs/{song_id}/stream is public and supports range requests for efficient playback."
    ),
    version="2.0.0",
    openapi_tags=openapi_tags,
)

# CORS: allow React dev server + configurable origin via env.
# Note: credentials=true requires explicit origins (not '*') in browsers, so we include common local dev URLs.
# Add additional origins via:
# - CORS_ALLOW_ORIGINS (our documented var), OR
# - ALLOWED_ORIGINS (platform/env commonly provided), as comma-separated values.
cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

_allow_origins_raw = _os.getenv("CORS_ALLOW_ORIGINS") or _os.getenv("ALLOWED_ORIGINS", "")
extra_origins = [o.strip() for o in _allow_origins_raw.split(",") if o.strip()]
cors_origins.extend(extra_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(songs_router)


@app.get(
    "/",
    summary="Health check",
    description="Simple health check endpoint.",
    tags=["Health"],
)
def health_check():
    """Return basic service health information."""
    return {"status": "ok"}
