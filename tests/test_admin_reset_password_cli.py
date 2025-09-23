import pytest
from sqlalchemy import select

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
