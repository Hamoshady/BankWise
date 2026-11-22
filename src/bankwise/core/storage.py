"""Short-lived private storage for sensitive uploads (for example ID card images).

Privacy by default: callers never choose file names (files get random 128-bit ids, so no path ever comes from
user input), files are owner-only, nothing is overwritten, size and type are checked, and every file expires
after a short retention period (default one hour) once `purge_expired()` runs.

Also enforced: a cap on the number of stored files, a vault directory that must be owner-only (POSIX), expired
files removed at start-up, and symbolic links never followed or overwritten.

NOT provided: encryption at rest (needs a crypto dependency and key management: part of the secure-storage
phase), automatic scheduling of the purge (call it at start-up and periodically), or reliable secure deletion
(overwrite-then-unlink is best effort on SSDs and journaling file systems). Windows ignores POSIX modes, so
protect the directory with ACLs there."""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from bankwise.core.errors import ConfigError, InvalidInputError

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FILE_RE = re.compile(r"^[0-9a-f]{32}\.bin$")
_SIGNATURES = (("jpeg", b"\xff\xd8\xff"), ("png", b"\x89PNG\r\n\x1a\n"))


@dataclass(frozen=True)
class StorageSettings:
    base_dir: str = "outputs/secure"
    ttl_seconds: int = 3600
    max_bytes: int = 10 * 1024 * 1024
    max_files: int = 1000

    def __post_init__(self) -> None:
        if not isinstance(self.base_dir, str) or not self.base_dir.strip() or "\x00" in self.base_dir:
            raise ConfigError("storage base_dir must be a non-empty string")
        for label, value, low, high in (("ttl_seconds", self.ttl_seconds, 1, 7 * 86400),
                                        ("max_bytes", self.max_bytes, 1, 100 * 1024 * 1024),
                                        ("max_files", self.max_files, 1, 100000)):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ConfigError(f"storage {label} must be an integer between {low} and {high}")


def detect_image_type(data: bytes) -> Optional[str]:
    """'jpeg' or 'png' judged by magic bytes only. This is not decoding: pixel-size limits belong to the image step."""
    for name, signature in _SIGNATURES:
        if data.startswith(signature):
            return name
    return None


class SecureFileStore:
    def __init__(self, settings: StorageSettings, root: Optional[Path] = None,
                 clock: Callable[[], float] = time.time, purge_on_start: bool = True) -> None:
        self._settings = settings
        self._clock = clock
        base = Path(settings.base_dir)
        self._dir = (base if base.is_absolute() else (root or Path.cwd()) / base)
        if self._dir.is_symlink():
            raise ConfigError("storage directory must not be a symlink")
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._dir = self._dir.resolve()
        self._check_directory()
        if purge_on_start:
            self.purge_expired()                     # nothing sensitive is left behind by an earlier run

    def save(self, data: bytes, require_image: bool = True) -> str:
        """Store bytes and return an opaque id. Images must be JPEG or PNG when `require_image` is True."""
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise InvalidInputError("nothing to store")
        if len(data) > self._settings.max_bytes:
            raise InvalidInputError("file is too large")
        if require_image and detect_image_type(bytes(data)) is None:
            raise InvalidInputError("only JPEG and PNG images are accepted")
        if self._count_files() >= self._settings.max_files:
            self.purge_expired()
            if self._count_files() >= self._settings.max_files:
                raise InvalidInputError("storage is full")
        file_id = uuid.uuid4().hex
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self._path(file_id), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        return file_id

    def load(self, file_id: str) -> bytes:
        path = self._path(file_id)
        if path.is_symlink() or not path.is_file() or self._expired(path):
            self._discard(path)
            raise InvalidInputError("file not found")
        return path.read_bytes()

    def delete(self, file_id: str) -> bool:
        path = self._path(file_id)
        existed = path.is_file() or path.is_symlink()
        self._discard(path)
        return existed

    def purge_expired(self) -> int:
        """Delete every expired file in the directory (only files this store created). Returns how many."""
        removed = 0
        for path in self._dir.iterdir():
            if not _FILE_RE.match(path.name):
                continue
            if path.is_symlink() or (path.is_file() and self._expired(path)):
                self._discard(path)
                removed += 1
        return removed

    # ------------------------------------------------------------------ internals
    def _check_directory(self) -> None:
        """POSIX: the vault must belong to this user and not be accessible to group/others (it is tightened to 0700
        when we can; otherwise startup fails). Windows: protect the directory with ACLs instead."""
        if not hasattr(os, "geteuid"):
            return
        info = self._dir.stat()
        if info.st_uid != os.geteuid():
            raise ConfigError("storage directory is owned by another user")
        if info.st_mode & 0o077:
            os.chmod(self._dir, 0o700)
            if self._dir.stat().st_mode & 0o077:
                raise ConfigError("storage directory is accessible to other users")

    def _count_files(self) -> int:
        return sum(1 for entry in self._dir.iterdir() if _FILE_RE.match(entry.name))

    def _path(self, file_id: str) -> Path:
        if not isinstance(file_id, str) or not _ID_RE.match(file_id):
            raise InvalidInputError("invalid file id")
        return self._dir / f"{file_id}.bin"

    def _expired(self, path: Path) -> bool:
        return self._clock() - path.stat().st_mtime >= self._settings.ttl_seconds

    @staticmethod
    def _discard(path: Path) -> None:
        """Remove a file. Content is overwritten first (best effort) ONLY for a plain single-link regular file: a
        symbolic link or hard-linked file is just unlinked, so a planted link can never make us zero someone
        else's data."""
        try:
            info = path.lstat()
            if path.is_symlink() or info.st_nlink > 1:
                path.unlink()
                return
            with open(path, "r+b") as handle:
                handle.write(b"\x00" * info.st_size)
                handle.flush()
                os.fsync(handle.fileno())
        except FileNotFoundError:
            return
        except OSError:
            pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass
