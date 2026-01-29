"""
Song endpoints (public):
- POST /songs/upload (multipart mp3 upload)
- GET /songs (list all songs)
- GET /songs/{id}/stream (public streaming)

Authentication has been removed from the backend; these endpoints are intentionally
public to keep upload, list, and playback flows working without tokens.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError

from src.api.db import get_db_session
from src.api.models import Song
from src.api.schemas import SongResponse, SongUploadResponse

router = APIRouter(tags=["Songs"])

_MAX_FILE_BYTES_DEFAULT = 50 * 1024 * 1024  # 50MB


def _media_root() -> Path:
    # Default to container-local media directory. Override via env if desired.
    return Path(os.getenv("MEDIA_ROOT", "media")).resolve()


def _max_file_bytes() -> int:
    try:
        return int(os.getenv("MAX_UPLOAD_BYTES", str(_MAX_FILE_BYTES_DEFAULT)))
    except ValueError:
        return _MAX_FILE_BYTES_DEFAULT


def _sanitize_filename(name: str) -> str:
    # Keep it simple and safe: letters, numbers, dot, dash, underscore.
    name = name.strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name or "upload.mp3"


def _validate_mp3(upload: UploadFile, content: bytes) -> Tuple[str, int]:
    """
    Basic mp3 validation.

    We accept:
    - Content-Type includes audio/mpeg OR application/octet-stream (some browsers)
    - Extension .mp3
    - Optional magic for ID3 header ("ID3") or MPEG frame sync (0xFFEx)
    """
    filename = upload.filename or "upload.mp3"
    safe_name = _sanitize_filename(filename)

    if not safe_name.lower().endswith(".mp3"):
        raise HTTPException(status_code=400, detail="Only .mp3 files are supported.")

    content_type = (upload.content_type or "").lower()
    if content_type and ("audio/mpeg" not in content_type) and ("application/octet-stream" not in content_type):
        raise HTTPException(status_code=400, detail="Invalid content type; expected audio/mpeg.")

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(content) > _max_file_bytes():
        raise HTTPException(status_code=413, detail="File too large.")

    head = content[:10]
    is_id3 = head.startswith(b"ID3")
    is_mpeg = len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    if not (is_id3 or is_mpeg):
        # Basic check only; don't be overly strict.
        raise HTTPException(status_code=400, detail="File does not look like a valid mp3.")

    return safe_name, len(content)


@router.get(
    "/songs",
    response_model=List[SongResponse],
    summary="List all songs",
    description="Returns all songs in the library, newest first.",
    operation_id="list_songs",
)
def list_songs() -> List[SongResponse]:
    """List all songs in the library (public)."""
    try:
        with get_db_session() as db:
            songs = db.execute(select(Song).order_by(desc(Song.created_at))).scalars().all()
            return [
                SongResponse(
                    id=s.id,
                    title=s.title,
                    artist=s.artist,
                    created_at=s.created_at,
                    size_bytes=int(s.size_bytes),
                    content_type=s.content_type,
                )
                for s in songs
            ]
    except (RuntimeError, SQLAlchemyError) as exc:
        # RuntimeError: missing DB configuration in src/api/db.py
        # SQLAlchemyError: connection / query failures
        raise HTTPException(
            status_code=500,
            detail=(
                "Backend database error while listing songs. "
                "Verify DATABASE_URL or POSTGRES_* env vars are configured for the backend. "
                f"({exc.__class__.__name__})"
            ),
        )


@router.post(
    "/songs/upload",
    response_model=SongUploadResponse,
    summary="Upload an mp3",
    description="Uploads an mp3 file. Stores file on disk and metadata in DB.",
    operation_id="upload_song",
)
def upload_song(
    file: UploadFile = File(..., description="MP3 file upload (multipart/form-data)"),
    title: Optional[str] = Form(None, description="Optional title. Defaults to original filename stem."),
    artist: Optional[str] = Form(None, description="Optional artist. Defaults to 'Unknown Artist'."),
) -> SongUploadResponse:
    """Upload an mp3 with basic validation and metadata (public)."""
    content = file.file.read()
    safe_name, size_bytes = _validate_mp3(file, content)

    # Fill defaults
    final_title = (title or Path(safe_name).stem).strip() or "Untitled"
    final_artist = (artist or "Unknown Artist").strip() or "Unknown Artist"

    # Store to disk
    media_root = _media_root()
    media_root.mkdir(parents=True, exist_ok=True)

    song_id = uuid.uuid4()
    stored_filename = f"{song_id}_{safe_name}"
    stored_path = media_root / stored_filename

    try:
        stored_path.write_bytes(content)
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to store file.")

    now = datetime.now(timezone.utc)
    content_type = (file.content_type or "audio/mpeg").lower()

    try:
        with get_db_session() as db:
            song = Song(
                id=song_id,
                user_id=None,  # auth removed; songs are not user-scoped anymore
                title=final_title,
                artist=final_artist,
                filename=stored_filename,
                content_type=content_type,
                size_bytes=size_bytes,
                duration_seconds=None,
                created_at=now,
            )
            db.add(song)
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Backend database error while saving uploaded song metadata. "
                "Verify DATABASE_URL or POSTGRES_* env vars are configured for the backend. "
                f"({exc.__class__.__name__})"
            ),
        )

    return SongUploadResponse(
        id=song_id,
        title=final_title,
        artist=final_artist,
        filename=stored_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        duration_seconds=None,
        created_at=now,
    )


@router.get(
    "/songs/{song_id}/stream",
    summary="Stream a song",
    description="Streams the mp3 file (public).",
    operation_id="stream_song",
    responses={
        200: {"content": {"audio/mpeg": {}}},
        404: {"description": "Not found"},
    },
)
def stream_song(song_id: uuid.UUID):
    """Serve a song file by id (public)."""
    try:
        with get_db_session() as db:
            song = db.execute(select(Song).where(Song.id == song_id)).scalar_one_or_none()
            if not song:
                raise HTTPException(status_code=404, detail="Song not found.")
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Backend database error while loading song metadata. "
                "Verify DATABASE_URL or POSTGRES_* env vars are configured for the backend. "
                f"({exc.__class__.__name__})"
            ),
        )

    media_path = _media_root() / song.filename
    if not media_path.exists():
        raise HTTPException(status_code=404, detail="File missing on server.")

    # FileResponse supports range requests in Starlette for efficient streaming.
    return FileResponse(
        path=str(media_path),
        media_type=song.content_type or "audio/mpeg",
        filename=f"{song.title}.mp3",
    )
