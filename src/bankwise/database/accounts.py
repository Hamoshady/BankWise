"""Credential verification and account checks; token signing remains in SecurityService."""
import re
import unicodedata
from uuid import UUID

from sqlalchemy import select

from bankwise.core.errors import AuthenticationError, InvalidInputError
from bankwise.core.passwords import hash_password, password_needs_rehash, verify_password_or_dummy
from bankwise.core.security import Principal
from bankwise.database.models import User, UserRole


def normalize_username(value: str) -> str:
    name = unicodedata.normalize("NFKC", value).strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", name):
        raise InvalidInputError("username must be 3-64 plain characters")
    return name


class Accounts:
    def __init__(self, database):
        self.database = database

    def verify(self, username: str, password: str):
        with self.database.session() as session, session.begin():
            user = session.scalar(select(User).where(User.username == normalize_username(username)))
            matched = verify_password_or_dummy(user.password_hash if user is not None else None, password)
            if not matched or user is None or not user.active:
                return None
            if password_needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)
            return Principal(str(user.id), frozenset(role.role for role in user.roles))

    def validate_principal(self, principal: Principal):
        try:
            user_id = UUID(principal.subject_id)
        except ValueError:
            raise AuthenticationError() from None
        with self.database.session() as session:
            user = session.get(User, user_id)
            if user is None or not user.active or frozenset(r.role for r in user.roles) != principal.roles:
                raise AuthenticationError()


def create_user(session, username, password, roles, policy):
    """Operator-only provisioning; no public registration or self-assigned privileges."""
    if not roles or not set(roles) <= policy.roles.keys():
        raise InvalidInputError("specify existing RBAC roles")
    user = User(username=normalize_username(username), password_hash=hash_password(password),
                roles=[UserRole(role=r) for r in sorted(set(roles))])
    session.add(user)
    session.flush()
    return user
