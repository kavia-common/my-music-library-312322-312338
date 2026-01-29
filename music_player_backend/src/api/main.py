from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes_auth import router as auth_router
from src.api.routes_songs import router as songs_router

openapi_tags = [
    {"name": "Auth", "description": "User registration and login (JWT)."},
    {"name": "Songs", "description": "Upload, list, and stream user-owned mp3 files."},
]

app = FastAPI(
    title="Music Player Backend API",
    description=(
        "Backend for a personal music library.\n\n"
        "Authentication:\n"
        "- Obtain a token via POST /auth/login (or /auth/register)\n"
        "- Send it on subsequent requests: Authorization: Bearer <token>\n\n"
        "Streaming:\n"
        "- GET /songs/{song_id}/stream requires Authorization header and only serves owned songs."
    ),
    version="1.0.0",
    openapi_tags=openapi_tags,
)

# CORS: allow React dev server + configurable origin via env.
# Note: credentials=true requires explicit origins (not '*') in browsers, so we include common local dev URLs.
# Add additional origins via CORS_ALLOW_ORIGINS as comma-separated values.
cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
import os as _os

extra_origins = [o.strip() for o in (_os.getenv("CORS_ALLOW_ORIGINS", "")).split(",") if o.strip()]
cors_origins.extend(extra_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(songs_router)


@app.get(
    "/",
    summary="Health check",
    description="Simple health check endpoint.",
    tags=["Auth"],
)
def health_check():
    return {"message": "Healthy"}
