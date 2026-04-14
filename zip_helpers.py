"""Shared helpers for creating Gradescope-compatible ZIP archives."""

import zipfile

# Unix file attribute bits for ZIP entries
_UNIX_EXEC_ATTR = 0o755 << 16
_UNIX_READ_ATTR = 0o644 << 16
_ZIP_UNIX_CREATE_SYSTEM = 3  # Unix


def write_to_zip(
    zf: zipfile.ZipFile,
    name: str,
    content: str | bytes,
    *,
    executable: bool = False,
) -> None:
    """Write a file to a ZIP archive with proper Unix attributes."""
    zi = zipfile.ZipInfo(name)
    zi.create_system = _ZIP_UNIX_CREATE_SYSTEM
    zi.external_attr = _UNIX_EXEC_ATTR if executable else _UNIX_READ_ATTR
    zf.writestr(zi, content)
