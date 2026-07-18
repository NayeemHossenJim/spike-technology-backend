from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class UUIDPrimaryKey(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)


class TimestampedModel(SQLModel):
    created_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": utc_now},
    )
