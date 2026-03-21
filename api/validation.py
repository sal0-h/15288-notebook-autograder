"""Shared request validation for API routes."""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import HTTPException


def parse_student_name_path_param(raw: str) -> str:
    """Decode and validate a student name from a URL path segment.

    Raises ``HTTPException`` (400) if the value looks like path traversal.
    """
    name = unquote(raw)
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    return name
