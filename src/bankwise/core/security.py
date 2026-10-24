"""Authorization (RBAC), explicit access to raw sensitive values, and safe output paths.

Authentication is deliberately NOT implemented here. A `Principal` is a claim about who is acting; creating
one does not prove anything. Only a future authentication layer (verified token, session, ...) may create
Principals for real callers. This module only answers "is this principal allowed to do X?", and the answer is
deny unless a configured role grants the permission."""
from __future__ import annotations

import errno
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, FrozenSet, List, Mapping, Optional

from bankwise.core.audit import AuditEvent, AuditOutcome, AuditSink
from bankwise.core.errors import BankWiseError, ConfigError, ErrorCode, InvalidInputError, PermissionDenied
from bankwise.core.pii import Sensitive

VIEW_SENSITIVE = "identity:view_sensitive"
_PERMISSION_RE = re.compile(r"^[a-z][a-z_]*:[a-z][a-z_]*$")
_WILDCARD_RE = re.compile(r"^[a-z][a-z_]*:\*$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
MAX_OUTPUT_BYTES = 5 * 1024 * 1024
_FORBIDDEN_NAME_CHARS = frozenset('<>:"|?*')            # ':' also blocks NTFS alternate data streams ("a.txt:hidden")
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)}
                              | {f"lpt{i}" for i in range(1, 10)})
_log = logging.getLogger("bankwise.security")


# ------------------------------------------------------------------ policy
@dataclass(frozen=True)
class RbacPolicy:
    permissions: FrozenSet[str]
    roles: Mapping[str, FrozenSet[str]]

    @staticmethod
    def from_mapping(raw: object) -> "RbacPolicy":
        if not isinstance(raw, dict) or set(raw) != {"permissions", "roles"}:
            raise ConfigError("rbac config must contain exactly 'permissions' and 'roles'")
        permissions = raw["permissions"]
        if not isinstance(permissions, list) or not permissions or len(set(permissions)) != len(permissions):
            raise ConfigError("rbac 'permissions' must be a non-empty list without duplicates")
        for permission in permissions:
            if not isinstance(permission, str) or not _PERMISSION_RE.match(permission):
                raise ConfigError("rbac permission names must look like 'domain:action'")
        namespaces = {permission.split(":")[0] for permission in permissions}
        roles_raw = raw["roles"]
        if not isinstance(roles_raw, dict):
            raise ConfigError("rbac 'roles' must be a mapping")
        roles: Dict[str, FrozenSet[str]] = {}
        for role, grants in roles_raw.items():
            if not isinstance(role, str) or not _ROLE_RE.match(role):
                raise ConfigError("rbac role names must be lowercase identifiers")
            if not isinstance(grants, list) or len(set(map(str, grants))) != len(grants):
                raise ConfigError(f"rbac role '{role}' must be a list of unique permissions")
            for grant in grants:
                if not isinstance(grant, str):
                    raise ConfigError(f"rbac role '{role}' has a non-string permission")
                if _WILDCARD_RE.match(grant):
                    if grant.split(":")[0] not in namespaces:
                        raise ConfigError(f"rbac role '{role}' grants a wildcard for an unknown domain")
                elif grant not in permissions:
                    raise ConfigError(f"rbac role '{role}' grants unknown permission '{grant}'")
            roles[role] = frozenset(grants)
        return RbacPolicy(frozenset(permissions), roles)


@dataclass(frozen=True)
class Principal:
    """A claim about the acting identity. NOT proof of authentication (see module docstring)."""

    subject_id: str
    roles: FrozenSet[str]

    def __post_init__(self) -> None:
        if not isinstance(self.subject_id, str) or not _SUBJECT_RE.match(self.subject_id):
            raise InvalidInputError("subject_id must be a short plain identifier")
        object.__setattr__(self, "roles", frozenset(self.roles))


class Authorizer:
    def __init__(self, policy: RbacPolicy) -> None:
        self.policy = policy

    def is_allowed(self, principal: Optional[Principal], permission: str) -> bool:
        """Deny by default: no principal, unknown permission, unknown role, or no grant all mean False."""
        if not isinstance(principal, Principal) or not isinstance(permission, str):
            return False
        if permission not in self.policy.permissions:
            return False
        wildcard = permission.split(":")[0] + ":*"
        for role in principal.roles:
            grants = self.policy.roles.get(role)
            if grants is not None and (permission in grants or wildcard in grants):
                return True
        return False

    def require(self, principal: Optional[Principal], permission: str, sink: Optional[AuditSink] = None) -> None:
        if self.is_allowed(principal, permission):
            return
        if sink is not None:
            try:
                sink.write(AuditEvent(
                    "authz.denied", AuditOutcome.DENIED,
                    actor_id=principal.subject_id if isinstance(principal, Principal) else None,
                    error_code=ErrorCode.PERMISSION_DENIED.value,
                    metadata={"permission": permission if isinstance(permission, str)
                              and _PERMISSION_RE.match(permission) else "invalid"},
                ))
            except Exception as error:
                _log.error("audit write failed while denying a request (%s)", type(error).__name__)
        raise PermissionDenied(f"permission '{permission}' denied")


def reveal_sensitive(value: Sensitive, principal: Optional[Principal], authorizer: Authorizer,
                     reason: str, sink: AuditSink) -> Any:
    """The explicit, audited way to read a raw sensitive value. It needs the 'identity:view_sensitive'
    permission and a short reason, and the value is released only after the audit event has been written."""
    if not isinstance(value, Sensitive):
        raise InvalidInputError("reveal_sensitive expects a Sensitive value")
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidInputError("a non-empty reason is required to reveal a sensitive value")
    authorizer.require(principal, VIEW_SENSITIVE, sink)
    try:
        sink.write(AuditEvent("pii.reveal", AuditOutcome.SUCCESS, actor_id=principal.subject_id,
                              metadata={"reason": reason}))
    except InvalidInputError:
        raise
    except Exception as error:
        raise BankWiseError(f"audit write failed: {type(error).__name__}") from None
    return value.reveal()


# ------------------------------------------------------------------ output paths
def safe_output_path(base_dir: "str | os.PathLike[str]", relative: "str | os.PathLike[str]") -> Path:
    """Resolve `relative` inside `base_dir` or raise. Rejects absolute paths, drive letters, UNC paths,
    '..', empty names, NUL bytes, Windows reserved device names and anything that resolves (for example
    through a symlink) outside the base directory."""
    try:
        text = os.fspath(relative)
    except TypeError:
        raise InvalidInputError("output path must be a string") from None
    if not isinstance(text, str) or not text or "\x00" in text:
        raise InvalidInputError("output path is empty or contains a NUL byte")
    for flavour in (PureWindowsPath(text), PurePosixPath(text)):
        if flavour.is_absolute() or flavour.drive or flavour.root:
            raise InvalidInputError("output path must be relative")
    parts: List[str] = [part for part in re.split(r"[\\/]+", text) if part not in ("", ".")]
    if not parts:
        raise InvalidInputError("output path is empty")
    for part in parts:
        if part == ".." or part != part.rstrip(" .") or part.split(".")[0].lower() in _WINDOWS_RESERVED \
                or any(char in _FORBIDDEN_NAME_CHARS or ord(char) < 32 for char in part):
            raise InvalidInputError("output path contains a forbidden component")
    base = Path(base_dir).resolve()
    target = base.joinpath(*parts).resolve()
    if target == base or not target.is_relative_to(base):
        raise InvalidInputError("output path escapes the output directory")
    return target


def write_private_text(path: Path, text: str, overwrite: bool = False, encoding: str = "utf-8",
                       max_bytes: int = MAX_OUTPUT_BYTES) -> None:
    """Create a file readable only by its owner (mode 0o600 on POSIX; Windows only honours the read-only
    bit, so rely on directory ACLs there). Refuses to overwrite unless `overwrite=True`, refuses to follow a
    symbolic link at the final path component (POSIX), caps the size, writes bytes (no newline translation) and
    removes a half-written file if the write fails."""
    data = text.encode(encoding)
    if len(data) > max_bytes:
        raise InvalidInputError("output is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL) \
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise InvalidInputError("output file already exists") from None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise InvalidInputError("output path is a symbolic link") from None
        raise
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
