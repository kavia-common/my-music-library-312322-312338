"""
Pydantic models (request/response shapes) for API endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, EmailStr


class AuthRegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="User email address (unique).")
    password: str = Field(..., min_length=6, description="User password (min 6 chars).")


class AuthLoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email address.")
    password: str = Field(..., description="User password.")


class AuthTokenResponse(BaseModel):
    token: str = Field(..., description="JWT access token.")
    token_type: str = Field("bearer", description="Token type for Authorization header.")


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
