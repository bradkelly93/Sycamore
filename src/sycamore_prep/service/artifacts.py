"""Download-token registry — maps engine output files to opaque tokens so the UI
never handles absolute paths.

Tokens are content-addressed (sha256 of the resolved path). ``register`` refuses
any path outside the whitelisted roots (``cache_dir`` / ``models_dir`` /
``raw_dir``), and ``resolve`` re-checks containment before returning — defeating
``../../`` traversal and symlink escapes on the FastAPI ``/download/{token}`` route.
"""

from __future__ import annotations

import base64
import hashlib
import threading
from pathlib import Path

from ..config import cache_dir, models_dir, raw_dir
from .viewmodels import ArtifactRef

_REGISTRY: dict[str, Path] = {}
_LOCK = threading.Lock()


def _roots() -> list[Path]:
    return [cache_dir().resolve(), models_dir().resolve(), raw_dir().resolve()]


def _within_whitelist(p: Path) -> bool:
    rp = p.resolve()
    return any(rp == root or rp.is_relative_to(root) for root in _roots())


def _token_for(p: Path) -> str:
    digest = hashlib.sha256(str(p).encode("utf-8")).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def register(path: Path | str | None, kind: str | None = None) -> ArtifactRef | None:
    """Register an engine artifact and return an :class:`ArtifactRef`. Returns
    ``None`` for a missing path; raises ``ValueError`` if the path escapes the
    whitelist (a programming error — engines only ever write under the roots)."""
    if path is None:
        return None
    p = Path(path).resolve()
    if not _within_whitelist(p):
        raise ValueError(f"refusing to register path outside whitelist: {p}")
    token = _token_for(p)
    with _LOCK:
        _REGISTRY[token] = p
    if kind is None:
        kind = "folder" if p.is_dir() else (p.suffix.lstrip(".").lower() or "file")
    return ArtifactRef(kind=kind, filename=p.name, token=token)


def resolve(token: str) -> Path:
    """Resolve a token to a real path, re-checking whitelist containment. Raises
    ``KeyError`` (forged/unknown token) or ``PermissionError`` (out-of-whitelist)."""
    with _LOCK:
        p = _REGISTRY.get(token)
    if p is None:
        raise KeyError(token)
    rp = p.resolve()
    if not _within_whitelist(rp):
        raise PermissionError(f"token resolves outside whitelist: {rp}")
    return rp
