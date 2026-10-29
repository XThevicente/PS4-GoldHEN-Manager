"""Minimal RFC 9110 single-range support for the built-in PKG streamer.

No multipart/byteranges, CDN redirects or network filesystem connectors.
Use for explicit local package files only. Range headers never define paths.
"""
from __future__ import annotations

import os
import re


class UnsatisfiableRange(ValueError):
    pass


def strong_etag(stat_result: os.stat_result) -> str:
    """File validator based on inode/size/mtime; not a cryptographic digest."""
    return (
        f'"{stat_result.st_size:x}-{stat_result.st_mtime_ns:x}-'
        f'{getattr(stat_result, "st_ino", 0):x}"'
    )


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return inclusive (start,end), or None if no range was requested.

    Invalid, unsupported multiple ranges and unsatisfiable ranges raise
    UnsatisfiableRange. Avoid parsing unbounded decimal strings.
    """
    if header is None or header == "":
        return None
    match = re.fullmatch(r"bytes=(\d{1,20})-(\d{0,20})|bytes=-(\d{1,20})", header)
    if not match or size <= 0:
        raise UnsatisfiableRange("Range inválido o recurso vacío")
    first, last, suffix = match.groups()
    if suffix is not None:
        amount = int(suffix)
        if amount <= 0:
            raise UnsatisfiableRange("Suffix no positivo")
        return max(size - amount, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise UnsatisfiableRange("Intervalo fuera del archivo")
    return start, min(end, size - 1)


def effective_range(
    range_header: str | None, if_range: str | None,
    size: int, etag: str
) -> tuple[int, int] | None:
    """If-Range with an unknown/old validator requests the entire resource."""
    if range_header and if_range is not None and if_range != etag:
        return None
    return byte_range(range_header, size)
