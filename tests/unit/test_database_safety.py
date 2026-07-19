from __future__ import annotations

import pytest

from tests.conftest import validate_test_database_url


def test_integration_database_guard_accepts_test_suffix() -> None:
    url = "postgresql+asyncpg://spike:password@localhost:5432/spike_test"
    assert validate_test_database_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://spike:password@localhost:5432/spike",
        "postgresql+asyncpg://spike:password@localhost:5432/production",
        "postgresql+asyncpg://spike:password@localhost:5432/",
    ],
)
def test_integration_database_guard_rejects_non_test_database(url: str) -> None:
    with pytest.raises(RuntimeError, match="ends with '_test'"):
        validate_test_database_url(url)
