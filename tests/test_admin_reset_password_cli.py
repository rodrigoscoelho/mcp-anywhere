import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from werkzeug.security import check_password_hash

from mcp_anywhere.auth.initialization import reset_user_password
from mcp_anywhere.auth.models import User


@pytest.mark.asyncio
async def test_reset_user_password_updates_hash(app):
    """Resetting the password should update the stored hash for the user."""

    async with app.state.get_async_session() as session:
        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        admin.set_password("OriginalPassword123!")
        session.add(admin)
        await session.commit()

    await reset_user_password("admin", "BrandNewPassword123!")

    async with app.state.get_async_session() as session:
        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        assert admin.check_password("BrandNewPassword123!")


@pytest.mark.asyncio
async def test_reset_user_password_missing_user_raises(app):
    """Attempting to reset a missing user should raise a ValueError."""

    # Ensure the target username does not exist
    async with app.state.get_async_session() as session:
        ghost = await session.scalar(select(User).where(User.username == "ghost"))
        if ghost is not None:
            await session.delete(ghost)
            await session.commit()

    with pytest.raises(ValueError, match="ghost"):
        await reset_user_password("ghost", "ValidPassword123!")


@pytest.mark.asyncio
async def test_reset_user_password_enforces_min_length(app):
    """Passwords shorter than the policy should be rejected."""

    async with app.state.get_async_session() as session:
        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        admin.set_password("KeepOldPassword123!")
        session.add(admin)
        await session.commit()

    with pytest.raises(ValueError, match="at least 12 characters"):
        await reset_user_password("admin", "shortpwd")

    # Confirm the password is unchanged
    async with app.state.get_async_session() as session:
        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        assert admin.check_password("KeepOldPassword123!")


def test_cli_reset_password_bootstraps_database(tmp_path):
    """The CLI should initialize the database before resetting the password."""

    data_dir = tmp_path / "data"
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)

    pythonpath_entries = [str(Path(__file__).resolve().parents[1] / "src")]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env["DATA_DIR"] = str(data_dir)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mcp_anywhere.__main__",
            "admin",
            "reset-password",
            "--password",
            "AdminSecret123!",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Password updated for user 'admin'." in result.stdout

    db_path = data_dir / "mcp_anywhere.db"
    assert db_path.exists(), "CLI should create the sqlite database file"

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT password_hash FROM users WHERE username = ?",
            ("admin",),
        ).fetchone()

    assert row is not None, "Admin user should be present after CLI reset"
    assert check_password_hash(row[0], "AdminSecret123!")
