from __future__ import annotations

import argparse
import asyncio

from sqlmodel import select

from app.core.security import hash_password
from app.db.session import async_session_factory, dispose_database
from app.models.user import User, UserRole


async def create_super_admin(*, email: str, full_name: str, password: str) -> None:
    async with async_session_factory() as session:  # type: AsyncSession
        existing = await session.execute(select(User).where(User.email == email.lower().strip()))
        if existing.scalar_one_or_none():
            raise SystemExit("A user with that email already exists.")
        session.add(
            User(
                email=email.lower().strip(),
                full_name=full_name.strip(),
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
    if len(args.password) < 12:
        raise SystemExit("Password must be at least 12 characters long.")
    try:
        await create_super_admin(email=args.email, full_name=args.full_name, password=args.password)
    finally:
        await dispose_database()


if __name__ == "__main__":
    asyncio.run(main())
