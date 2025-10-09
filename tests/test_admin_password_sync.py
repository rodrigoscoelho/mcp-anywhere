import pytest
from sqlalchemy import select

from mcp_anywhere.auth.initialization import create_default_admin_user
from mcp_anywhere.auth.models import User


@pytest.mark.asyncio
async def test_create_admin_uses_provided_password(db_session):
    password = "EnvPassword123!"
    user = await create_default_admin_user(
        username="admin",
        password=password,
        db_session=db_session,
    )

    assert user.check_password(password)

    stmt = select(User).where(User.username == "admin")
    stored_user = await db_session.scalar(stmt)
    assert stored_user is not None
    assert stored_user.check_password(password)


@pytest.mark.asyncio
async def test_create_admin_updates_password_when_changed(db_session):
    original_password = "InitialPass123!"
    updated_password = "UpdatedPass456!"

    first_user = await create_default_admin_user(
        username="admin",
        password=original_password,
        db_session=db_session,
    )
    assert first_user.check_password(original_password)

    second_user = await create_default_admin_user(
        username="admin",
        password=updated_password,
        db_session=db_session,
    )
    assert second_user.check_password(updated_password)
    assert second_user.id == first_user.id

    stmt = select(User).where(User.username == "admin")
    stored_user = await db_session.scalar(stmt)
    assert stored_user is not None
    assert stored_user.check_password(updated_password)


@pytest.mark.asyncio
async def test_create_admin_retains_password_when_no_env_password(db_session):
    password = "PersistedPass789!"
    await create_default_admin_user(
        username="admin",
        password=password,
        db_session=db_session,
    )

    user_without_env = await create_default_admin_user(
        username="admin",
        password=None,
        db_session=db_session,
    )

    assert user_without_env.check_password(password)
