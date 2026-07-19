from __future__ import annotations


def validate_password_strength(value: str) -> str:
    """Apply the shared password policy used by public and administrative accounts."""

    if not any(char.islower() for char in value):
        raise ValueError("Password must include a lowercase letter")
    if not any(char.isupper() for char in value):
        raise ValueError("Password must include an uppercase letter")
    if not any(char.isdigit() for char in value):
        raise ValueError("Password must include a number")
    return value
