"""
Pydantic models (request/response shapes) for API endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SongResponse(BaseModel):
    id: uuid.UUID = Field(..., description="Song UUID.")
    title: str = Field(..., description="Song title.")
    artist: str = Field(..., description="Song artist.")
    created_at: datetime = Field(..., description="Creation timestamp.")
    size_bytes: int = Field(..., description="File size in bytes.")
    content_type: str = Field(..., description="Content type stored at upload time.")


class SongUploadResponse(BaseModel):
    id: uuid.UUID = Field(..., description="Created song UUID.")
    title: str = Field(..., description="Song title.")
    artist: str = Field(..., description="Song artist.")
    filename: str = Field(..., description="Stored filename (server-side).")
    content_type: str = Field(..., description="Uploaded content type.")
    size_bytes: int = Field(..., description="Size in bytes.")
    duration_seconds: Optional[int] = Field(None, description="Duration in seconds if known.")
    created_at: datetime = Field(..., description="Creation timestamp.")
