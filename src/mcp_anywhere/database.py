"""Database models for MCP Anywhere with async support."""

import uuid
from datetime import datetime
from typing import Any, AsyncContextManager, Sequence

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from mcp_anywhere.base import Base
from mcp_anywhere.config import Config
from mcp_anywhere.logging_config import get_logger

logger = get_logger(__name__)


def generate_id() -> str:
    """Generate a unique 8-character ID."""
    return str(uuid.uuid4())[:8]


class MCPServer(Base):
    """Model for MCP server configurations."""

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_id)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    github_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    runtime_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # npx, uvx, docker
    install_command: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_command: Mapped[str] = mapped_column(Text, nullable=False)
    env_variables: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    build_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    build_error: Mapped[str | None] = mapped_column(Text)
    build_logs: Mapped[str | None] = mapped_column(Text)
    image_tag: Mapped[str | None] = mapped_column(String(200))

    # Relationship to tools
    tools: Mapped[list["MCPServerTool"]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )

    # Relationship to secret files
    secret_files: Mapped[list["MCPServerSecretFile"]] = relationship(
        back_populates="server", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<MCPServer {self.name}>"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "id": self.id,
            "name": self.name,
            "github_url": self.github_url,
            "description": self.description,
            "runtime_type": self.runtime_type,
            "install_command": self.install_command,
            "start_command": self.start_command,
            "env_variables": self.env_variables,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "build_status": self.build_status,
            "build_error": self.build_error,
            "build_logs": self.build_logs,
            "image_tag": self.image_tag,
        }


class MCPServerTool(Base):
    """Model for MCP server tools."""

    __tablename__ = "mcp_server_tools"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_id)
    server_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("mcp_servers.id"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_description: Mapped[str | None] = mapped_column(Text)
    tool_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    # Relationship back to server
    server: Mapped["MCPServer"] = relationship(back_populates="tools")

    def __repr__(self) -> str:
        return f"<MCPServerTool {self.tool_name}>"


class MCPServerSecretFile(Base):
    """Model for MCP server secret files."""

    __tablename__ = "mcp_server_secret_files"

    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=generate_id)
    server_id: Mapped[str] = mapped_column(
        String(8), ForeignKey("mcp_servers.id"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(50))
    file_size: Mapped[int | None] = mapped_column()
    env_var_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # Track last update (nullable for backward compatibility)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None, onupdate=datetime.utcnow
    )

    # Relationship back to server
    server: Mapped["MCPServer"] = relationship(back_populates="secret_files")

    def __repr__(self) -> str:
        return f"<MCPServerSecretFile {self.original_filename}>"

    def to_dict(self) -> dict:
        """Convert secret file to dictionary."""
        return {
            "id": self.id,
            "server_id": self.server_id,
            "original_filename": self.original_filename,
            "stored_filename": self.stored_filename,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "env_var_name": self.env_var_name,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ToolUsageLog(Base):
    """Persist MCP tool usage events for analytics and troubleshooting."""

    __tablename__ = "tool_usage_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    client_name: Mapped[str | None] = mapped_column(String(120))
    request_type: Mapped[str] = mapped_column(String(40), nullable=False)
    server_id: Mapped[str | None] = mapped_column(String(8))
    server_name: Mapped[str | None] = mapped_column(String(120))
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    full_tool_name: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return (
            f"<ToolUsageLog {self.full_tool_name} status={self.status} "
            f"at {self.timestamp.isoformat()}>"
        )


class AppSetting(Base):
    """Global application settings persisted in DB.

    Schema:
    - key (PK, text)
    - value (text, nullable)
    - encrypted (bool, default False)
    - updated_at (timestamp)
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<AppSetting {self.key} encrypted={self.encrypted}>"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "encrypted": self.encrypted,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DatabaseManager:
    """Manages database engine and session factory lifecycle."""

    def __init__(self) -> None:
        self._engine = None
        self._session_factory = None

    async def initialize(self) -> None:
        """Initialize the async database."""
        if self._engine is None:
            # Create engine - use SQLALCHEMY_DATABASE_URI from config
            db_url = Config.SQLALCHEMY_DATABASE_URI.replace(
                "sqlite://", "sqlite+aiosqlite://"
            )
            self._engine = create_async_engine(db_url)

            # Create session factory
            self._session_factory = async_sessionmaker(
                self._engine, class_=AsyncSession, expire_on_commit=False
            )

            # Create tables
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            # Backwards-compatibility: add missing columns that weren't present
            # in older database files (SQLite doesn't ALTER TABLE via metadata).
            # We check for the specific column used in code and add it if missing.
            async with self._engine.connect() as conn:
                try:
                    result = await conn.execute(
                        text("PRAGMA table_info('mcp_server_secret_files')")
                    )
                    rows = result.fetchall()
                    existing_cols = [row[1] for row in rows] if rows else []
                    if "updated_at" not in existing_cols:
                        # Add nullable updated_at column to existing table
                        await conn.execute(
                            text(
                                "ALTER TABLE mcp_server_secret_files ADD COLUMN updated_at DATETIME"
                            )
                        )
                        logger.info(
                            "Added missing column 'updated_at' to mcp_server_secret_files"
                        )

                    result = await conn.execute(
                        text("PRAGMA table_info('mcp_servers')")
                    )
                    rows = result.fetchall()
                    existing_cols = [row[1] for row in rows] if rows else []
                    if "build_logs" not in existing_cols:
                        await conn.execute(
                            text("ALTER TABLE mcp_servers ADD COLUMN build_logs TEXT")
                        )
                        logger.info("Added missing column 'build_logs' to mcp_servers")
                except Exception:
                    # Non-critical: log and continue. If the table doesn't exist yet,
                    # create_all above will handle it.
                    logger.debug(
                        "Could not inspect/alter mcp_server_secret_files table; continuing"
                    )

            logger.info("Async database initialized")

    def get_session(self) -> AsyncContextManager[AsyncSession]:
        """Get an async database session (async context manager)."""
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        # async_sessionmaker() returns an AsyncSession instance which is also usable
        # as an async context manager. Expose it as an AsyncContextManager for typing.
        return self._session_factory()

    async def close(self) -> None:
        """Close the database connection."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database connection closed")

    @property
    def is_initialized(self) -> bool:
        """Check if database is initialized."""
        return self._engine is not None


# Create a single instance
db_manager = DatabaseManager()


# Database session management - backward compatibility functions
async def init_db() -> None:
    """Initialize the async database."""
    await db_manager.initialize()


def get_async_session() -> AsyncContextManager[AsyncSession]:
    """Get an async database session (async context manager)."""
    return db_manager.get_session()


async def close_db() -> None:
    """Close the database connection."""
    await db_manager.close()


# Async helper functions to replace Flask-SQLAlchemy equivalents
async def get_active_servers(session: AsyncSession | None = None) -> Sequence[MCPServer]:
    """Get all active servers (async equivalent of Flask-SQLAlchemy function)."""
    if session:
        # Use provided session
        stmt = (
            select(MCPServer)
            .where(MCPServer.is_active)
            .options(selectinload(MCPServer.secret_files))
        )
        result = await session.execute(stmt)
        return result.scalars().all()
    else:
        # Create own session
        async with get_async_session() as session:
            stmt = (
                select(MCPServer)
                .where(MCPServer.is_active)
                .options(selectinload(MCPServer.secret_files))
            )
            result = await session.execute(stmt)
            return result.scalars().all()


async def get_built_servers(session: AsyncSession | None = None) -> Sequence[MCPServer]:
    """Get all built servers (async equivalent of Flask-SQLAlchemy function)."""
    if session:
        # Use provided session
        stmt = (
            select(MCPServer)
            .where(MCPServer.build_status == "built")
            .options(selectinload(MCPServer.secret_files))
        )
        result = await session.execute(stmt)
        return result.scalars().all()
    else:
        # Create own session
        async with get_async_session() as session:
            stmt = (
                select(MCPServer)
                .where(MCPServer.build_status == "built")
                .options(selectinload(MCPServer.secret_files))
            )
            result = await session.execute(stmt)
            return result.scalars().all()


# Import auth models after Base is defined to avoid circular imports
