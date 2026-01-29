"""
Song endpoints (public):
- POST /songs/upload (multipart mp3 upload)
- GET /songs (list all songs)
- GET /songs/{id}/stream (public streaming)

Authentication has been removed from the backend; these endpoints are intentionally
public to keep upload, list, and playback flows working without tokens.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.responses import StreamingResponse
from starlette.status import HTTP_404_NOT_FOUND
from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.api.db import db_session_dep, get_db_session
from src.api.models import Song
from src.api.schemas import SongResponse, SongUploadResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Songs"])

_MAX_FILE_BYTES_DEFAULT = 50 * 1024 * 1024  # 50MB

# Anchor paths to the backend container root (music_player_backend/), not the process CWD.
# This prevents preview/runtime working-directory differences from breaking streaming.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _media_root() -> Path:
    """
    Return the absolute directory where media files are stored.

    Resolution strategy:
    - If MEDIA_ROOT is an absolute path: use it.
    - If MEDIA_ROOT is relative or unset: resolve it relative to the backend container root.

    This is intentionally *not* based on the current working directory because the live
    preview runtime can start uvicorn from a different CWD than local dev/tests.
    """
    configured = os.getenv("MEDIA_ROOT", "media").strip() or "media"
    raw = Path(configured)

    if raw.is_absolute():
        root = raw
    else:
        root = (_BACKEND_ROOT / raw)

    # resolve() normalizes but we keep the anchor above stable.
    resolved = root.resolve()
    return resolved


def _json_404(detail: str) -> None:
    """Raise a JSON 404 error with a predictable shape."""
    raise HTTPException(
        status_code=HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "message": detail},
    )


def _resolve_song_media_path(song_filename: str) -> Path:
    """
    Resolve the on-disk path for a stored song filename.

    Rules:
    - If DB stored an absolute path, use it as-is.
    - If DB stored a relative path (possibly with subdirs), resolve it under MEDIA_ROOT.
    - Disallow path traversal outside MEDIA_ROOT for relative paths.
    """
    if not song_filename:
        _json_404("File missing on server.")

    raw = Path(song_filename)

    # Absolute path: trust but still check existence later.
    if raw.is_absolute():
        return raw

    media_root = _media_root()
    # Normalize (removes .. etc) then ensure it is still under media_root.
    candidate = (media_root / raw).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError:
        # Path traversal attempt or bad stored filename.
        _json_404("File missing on server.")

    return candidate


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


def _parse_range_header(range_header: str, file_size: int) -> Optional[Tuple[int, int]]:
    """
    Parse a single HTTP Range header ("bytes=start-end") for a file.

    Returns:
        (start, end) inclusive byte offsets if valid, else None.
    """
    if not range_header:
        return None

    # Example: "bytes=0-1023" or "bytes=100-" or "bytes=-500"
    if not range_header.startswith("bytes="):
        return None

    spec = range_header[len("bytes=") :].strip()
    # We only support a single range (no commas).
    if "," in spec:
        return None

    start_s, end_s = (spec.split("-", 1) + [""])[:2]
    start_s = start_s.strip()
    end_s = end_s.strip()

    try:
        if start_s == "" and end_s == "":
            return None

        if start_s == "":
            # suffix range: last N bytes
            suffix_len = int(end_s)
            if suffix_len <= 0:
                return None
            start = max(file_size - suffix_len, 0)
            end = file_size - 1
            return (start, end)

        start = int(start_s)
        if start < 0:
            return None

        if end_s == "":
            end = file_size - 1
        else:
            end = int(end_s)

        if end < start:
            return None
        if start >= file_size:
            return None

        end = min(end, file_size - 1)
        return (start, end)
    except ValueError:
        return None


def _iter_file_range(path: Path, start: int, end: int, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Yield bytes from file [start, end] inclusive."""
    with path.open("rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            to_read = min(chunk_size, remaining)
            chunk = f.read(to_read)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get(
    "/songs",
    response_model=List[SongResponse],
    summary="List all songs",
    description="Returns all songs in the library, newest first.",
    operation_id="list_songs",
)
def list_songs(db: Session = Depends(db_session_dep)) -> List[SongResponse]:
    """List all songs in the library (public)."""
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
    description="Streams the mp3 file (public). Supports HTTP Range requests.",
    operation_id="stream_song",
    responses={
        200: {"content": {"audio/mpeg": {}}},
        206: {"content": {"audio/mpeg": {}}},
        404: {"description": "Not found"},
    },
)
def stream_song(song_id: uuid.UUID, request: Request):
    """Serve a song file by id (public), with explicit Range support."""
    try:
        with get_db_session() as db:
            song = db.execute(select(Song).where(Song.id == song_id)).scalar_one_or_none()
            if not song:
                _json_404("Song not found.")
    except HTTPException:
        raise
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Backend database error while loading song metadata. "
                "Verify DATABASE_URL or POSTGRES_* env vars are configured for the backend. "
                f"({exc.__class__.__name__})"
            ),
        )

    media_root = _media_root()
    media_path = _resolve_song_media_path(song.filename)

    logger.info(
        "stream_song: song_id=%s filename=%s media_root=%s resolved_path=%s cwd=%s range=%s",
        str(song_id),
        song.filename,
        str(media_root),
        str(media_path),
        os.getcwd(),
        request.headers.get("range"),
    )

    if not media_path.is_file():
        _json_404("File missing on server.")

    try:
        file_size = media_path.stat().st_size
    except OSError:
        _json_404("File missing on server.")

    range_header = request.headers.get("range") or request.headers.get("Range")
    byte_range = _parse_range_header(range_header, file_size) if range_header else None

    headers = {
        "Accept-Ranges": "bytes",
        # Inline disposition to support <audio> playback without forced download.
        # Keep filename ASCII-safe.
        "Content-Disposition": f'inline; filename="{_sanitize_filename(song.title)}.mp3"',
    }

    if byte_range is None:
        # Full content
        headers["Content-Length"] = str(file_size)
        return StreamingResponse(
            _iter_file_range(media_path, 0, file_size - 1),
            media_type="audio/mpeg",
            headers=headers,
            status_code=200,
        )

    start, end = byte_range
    content_length = end - start + 1
    headers.update(
        {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
        }
    )
    return StreamingResponse(
        _iter_file_range(media_path, start, end),
        media_type="audio/mpeg",
        headers=headers,
        status_code=206,
    )
