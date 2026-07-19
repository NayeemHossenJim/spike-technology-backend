from __future__ import annotations

import argparse
import asyncio

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import select

from app.core.password_policy import validate_password_strength
from app.core.security import hash_password
from app.db.session import async_session_factory, dispose_database
from app.models.user import User, UserRole


async def create_super_admin(*, email: str, full_name: str, password: str) -> None:
    try:
        normalized_email = str(TypeAdapter(EmailStr).validate_python(email)).lower()
    except ValidationError as exc:
        raise SystemExit("A valid email address is required.") from exc

    normalized_name = full_name.strip()
    if len(normalized_name) < 2 or len(normalized_name) > 120:
        raise SystemExit("Full name must be between 2 and 120 characters.")
    if len(password) < 12 or len(password) > 128:
        raise SystemExit("Password must be between 12 and 128 characters long.")
    try:
        validate_password_strength(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    async with async_session_factory() as session:
        existing = await session.execute(select(User).where(User.email == normalized_email))
        if existing.scalar_one_or_none():
            raise SystemExit("A user with that email already exists.")
        session.add(
            User(
                email=normalized_email,
                full_name=normalized_name,
                password_hash=hash_password(password),
                role=UserRole.SUPER_ADMIN,
                is_verified=True,
            )
        )
        await session.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a verified Spike Technology super-admin user."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--password", required=True)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    try:
        await create_super_admin(email=args.email, full_name=args.full_name, password=args.password)
    finally:
        await dispose_database()


if __name__ == "__main__":
    asyncio.run(main())
