import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".gitleaks.toml"


def _load_config() -> dict:
    with CONFIG_PATH.open("rb") as handle:
        return tomllib.load(handle)


def test_gitleaks_policy_extends_defaults_without_disabling_rules() -> None:
    config = _load_config()

    assert config["extend"]["useDefault"] is True
    assert "disabledRules" not in config["extend"]
    assert "allowlists" not in config


def test_gitleaks_policy_only_extends_reviewed_rules() -> None:
    config = _load_config()

    rules = config["rules"]
    assert {rule["id"] for rule in rules} == {
        "curl-auth-header",
        "generic-api-key",
        "stripe-access-token",
    }


def test_gitleaks_allowlists_require_both_path_and_line_match() -> None:
    config = _load_config()

    for rule in config["rules"]:
        for allowlist in rule["allowlists"]:
            assert allowlist["condition"] == "AND"
            assert allowlist["regexTarget"] == "line"
            assert allowlist["paths"]
            assert allowlist["regexes"]

            assert "commits" not in allowlist
            assert "stopwords" not in allowlist


def test_gitleaks_policy_has_no_blanket_test_or_documentation_exclusion() -> None:
    config = _load_config()

    path_patterns = [
        pattern
        for rule in config["rules"]
        for allowlist in rule["allowlists"]
        for pattern in allowlist["paths"]
    ]

    forbidden = {
        "tests",
        "tests/",
        "README.md",
        ".*tests.*",
        ".*README.*",
    }

    assert forbidden.isdisjoint(path_patterns)


def test_stripe_test_allowlists_remain_length_bounded() -> None:
    config = _load_config()

    stripe = next(rule for rule in config["rules"] if rule["id"] == "stripe-access-token")

    rendered = "\n".join(
        pattern for allowlist in stripe["allowlists"] for pattern in allowlist["regexes"]
    )

    assert "sk_live_[a-z]{10}" in rendered
    assert "sk_test_[a-z]{11}" in rendered
    assert ".*sk_live_" not in rendered
    assert ".*sk_test_" not in rendered


def test_readme_password_allowlist_requires_explicit_placeholder_brackets() -> None:
    config = _load_config()

    generic = next(rule for rule in config["rules"] if rule["id"] == "generic-api-key")

    readme_allowlist = next(
        allowlist
        for allowlist in generic["allowlists"]
        if allowlist["description"] == "README password placeholder example only"
    )

    assert readme_allowlist["condition"] == "AND"
    assert readme_allowlist["regexTarget"] == "line"
    assert readme_allowlist["paths"] == [r"(?:^|/)README\.md$"]

    rendered = "\n".join(readme_allowlist["regexes"])

    assert "new_password" in rendered
    assert "<" in rendered
    assert ">" in rendered
    assert ".*new_password" not in rendered


GITLEAKS_IGNORE_PATH = ROOT / ".gitleaksignore"
README_PATH = ROOT / "README.md"

REVIEWED_HISTORICAL_FINGERPRINT = (
    "2aef5728505ace9ae4eb9edc2569e55490fcf950:README.md:generic-api-key:200"
)


def test_gitleaks_ignore_contains_only_reviewed_historical_fingerprint() -> None:
    entries = [
        line.strip()
        for line in GITLEAKS_IGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert entries == [REVIEWED_HISTORICAL_FINGERPRINT]


def test_readme_uses_password_placeholder_instead_of_test_fixture() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert '"new_password":"<NEW_PASSWORD>"' in readme
