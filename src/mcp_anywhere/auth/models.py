"""Authentication and authorization models for OAuth 2.0 implementation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from mcp_anywhere.base import Base


class User(Base):
    """User model for authentication."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    def set_password(self, password: str) -> None:
        """Set password with proper hashing."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Check if provided password matches the stored hash."""
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        """Convert user to dictionary representation."""
        return {
            "id": self.id,
            "username": self.username,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class OAuth2Client(Base):
    """OAuth 2.0 client model with support for dynamic registration and PKCE."""

    __tablename__ = "oauth2_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(48), unique=True, nullable=False, index=True
    )
    client_secret: Mapped[str | None] = mapped_column(  # Can be null for public clients
        String(120), nullable=True
    )
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    redirect_uri: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # Can store multiple URIs as JSON
    scope: Mapped[str] = mapped_column(String(255), nullable=False, default="read")
    grant_types: Mapped[str] = mapped_column(
        String(255), nullable=False, default="authorization_code"
    )
    response_types: Mapped[str] = mapped_column(
        String(255), nullable=False, default="code"
    )
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(50), nullable=False, default="client_secret_basic"
    )
    is_confidential: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        """Convert client to dictionary representation."""
        return {
            "id": self.id,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "grant_types": self.grant_types,
            "response_types": self.response_types,
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "is_confidential": self.is_confidential,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AuthorizationCode(Base):
    """Temporary authorization codes for OAuth 2.0 flow with PKCE support."""

    __tablename__ = "authorization_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("oauth2_clients.client_id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    code_challenge: Mapped[str | None] = mapped_column(String(128), nullable=True)
    code_challenge_method: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    def is_expired(self) -> bool:
        """Check if the authorization code has expired."""
        return datetime.utcnow() > self.expires_at

    def to_dict(self) -> dict:
        """Convert authorization code to dictionary representation."""
        return {
            "id": self.id,
            "code": self.code,
            "client_id": self.client_id,
            "user_id": self.user_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "code_challenge": self.code_challenge,
            "code_challenge_method": self.code_challenge_method,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_used": self.is_used,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class OAuth2Token(Base):
    """Persistent storage for OAuth 2.0 access tokens."""

    __tablename__ = "oauth2_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    token_type: Mapped[str] = mapped_column(String(20), nullable=False, default="Bearer")
    client_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("oauth2_clients.client_id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def is_expired(self) -> bool:
        """Check if the access token has expired."""
        return datetime.utcnow() > self.expires_at

    def is_valid(self) -> bool:
        """Check if the token is valid (not expired and not revoked)."""
        return not self.is_expired() and not self.is_revoked

    def to_dict(self) -> dict:
        """Convert token to dictionary representation."""
        return {
            "id": self.id,
            "token": self.token,
            "token_type": self.token_type,
            "client_id": self.client_id,
            "user_id": self.user_id,
            "scope": self.scope,
            "resource": self.resource,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_revoked": self.is_revoked,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
        }


class APIToken(Base):
    """API token for direct bearer authentication."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def to_dict(self) -> dict:
        """Convert token metadata to dictionary (never returns the raw token)."""
        return {
            "id": self.id,
            "name": self.name,
            "token_prefix": self.token_prefix,
            "token_hint": self.token_hint,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked": self.revoked,
        }


class OAuth2RefreshToken(Base):
    """Persistent storage for OAuth 2.0 refresh tokens."""

    __tablename__ = "oauth2_refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    access_token_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("oauth2_tokens.id"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("oauth2_clients.client_id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def is_expired(self) -> bool:
        """Check if the refresh token has expired."""
        if self.expires_at is None:
            return False  # Never expires
        return datetime.utcnow() > self.expires_at

    def is_valid(self) -> bool:
        """Check if the refresh token is valid (not expired and not revoked)."""
        return not self.is_expired() and not self.is_revoked

    def to_dict(self) -> dict:
        """Convert refresh token to dictionary representation."""
        return {
            "id": self.id,
            "token": self.token,
            "access_token_id": self.access_token_id,
            "client_id": self.client_id,
            "user_id": self.user_id,
            "scope": self.scope,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_revoked": self.is_revoked,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
        }


# Database indexes for optimal OAuth performance
Index(
    "idx_auth_codes_client_user", AuthorizationCode.client_id, AuthorizationCode.user_id
)
Index("idx_auth_codes_expires", AuthorizationCode.expires_at, AuthorizationCode.is_used)
Index("idx_tokens_client_user", OAuth2Token.client_id, OAuth2Token.user_id)
Index("idx_tokens_expires_revoked", OAuth2Token.expires_at, OAuth2Token.is_revoked)
Index(
    "idx_refresh_tokens_client_user",
    OAuth2RefreshToken.client_id,
    OAuth2RefreshToken.user_id,
)
Index(
    "idx_refresh_tokens_expires_revoked",
    OAuth2RefreshToken.expires_at,
    OAuth2RefreshToken.is_revoked,
)
