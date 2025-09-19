"""Utility helpers for issuing and managing API tokens used for direct Bearer auth."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_anywhere.auth.models import APIToken

_TOKEN_PREFIX_LEN = 8
_TOKEN_HINT_LEN = 4


def _hash_token(raw_token: str) -> str:
    """Return a SHA-256 hex digest for the provided token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _derive_prefix(raw_token: str) -> str:
    return raw_token[:_TOKEN_PREFIX_LEN]


def _derive_hint(raw_token: str) -> str:
    return raw_token[-_TOKEN_HINT_LEN:]


@dataclass(slots=True)
class IssuedToken:
    """Result wrapper returned when a new token is generated."""

    token: str
    metadata: APIToken


class APITokenService:
    """High-level helper to create, list and validate API tokens."""

    def __init__(self, get_session_factory):
        """Initialize with a callable returning an AsyncSession context manager."""
        self._get_session_factory = get_session_factory

    async def issue_token(self, *, name: str, created_by: int) -> IssuedToken:
        """Create a brand new API token and persist its hash.

        Returns the raw token (only once) along with the stored metadata.
        """
        raw_token = secrets.token_urlsafe(40)
        token_hash = _hash_token(raw_token)

        token = APIToken(
            name=name,
            token_hash=token_hash,
            token_prefix=_derive_prefix(raw_token),
            token_hint=_derive_hint(raw_token),
            created_by=created_by,
        )

        async with self._get_session_factory() as session:
            session.add(token)
            await session.commit()
            await session.refresh(token)

        return IssuedToken(token=raw_token, metadata=token)

    async def list_tokens(self) -> list[APIToken]:
        """Return non-revoked tokens ordered by creation date (desc)."""
        async with self._get_session_factory() as session:
            stmt = (
                select(APIToken)
                .where(APIToken.revoked.is_(False))
                .order_by(APIToken.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def revoke_token(self, token_id: int) -> bool:
        """Soft-delete the provided token.

        Returns True if a record was updated, False otherwise.
        """
        async with self._get_session_factory() as session:
            db_token = await session.get(APIToken, token_id)
            if not db_token or db_token.revoked:
                return False

            db_token.revoked = True
            db_token.last_used_at = datetime.utcnow()
            session.add(db_token)
            await session.commit()
            return True

    async def validate(self, raw_token: str) -> APIToken | None:
        """Return token metadata if the provided raw token matches a stored hash."""
        token_hash = _hash_token(raw_token)
        async with self._get_session_factory() as session:
            stmt = select(APIToken).where(
                APIToken.token_hash == token_hash, APIToken.revoked.is_(False)
            )
            result = await session.execute(stmt)
            api_token = result.scalar_one_or_none()
            if not api_token:
                return None

            api_token.last_used_at = datetime.utcnow()
            session.add(api_token)
            await session.commit()
            return api_token

    async def find_by_id(self, token_id: int) -> APIToken | None:
        async with self._get_session_factory() as session:
            return await session.get(APIToken, token_id)
