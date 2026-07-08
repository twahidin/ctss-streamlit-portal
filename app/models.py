"""Pydantic schemas for request/response shapes."""

from __future__ import annotations

from pydantic import BaseModel


class VerifyRequest(BaseModel):
    code: str


class VerifyResponse(BaseModel):
    ok: bool
    group_id: str | None = None
    group_name: str | None = None
    url: str | None = None
    error: str | None = None


class UploadResult(BaseModel):
    ok: bool
    commit_sha: str | None = None
    url: str | None = None
    errors: list[str] = []
