from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"

EXPECTED_ECOSYSTEMS = {
    "uv",
    "github-actions",
    "docker",
    "docker-compose",
}


def load_dependabot() -> dict:
    return yaml.load(
        DEPENDABOT_PATH.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def updates_by_ecosystem() -> dict[str, dict]:
    config = load_dependabot()

    return {update["package-ecosystem"]: update for update in config["updates"]}


def test_dependabot_configuration_uses_version_two() -> None:
    config = load_dependabot()

    assert config["version"] == "2"


def test_dependabot_monitors_all_repository_dependency_ecosystems() -> None:
    updates = updates_by_ecosystem()

    assert set(updates) == EXPECTED_ECOSYSTEMS


def test_dependabot_version_updates_target_develop() -> None:
    updates = updates_by_ecosystem()

    for update in updates.values():
        assert update["target-branch"] == "develop"
        assert update["directory"] == "/"


def test_dependabot_updates_are_weekly_and_bounded() -> None:
    updates = updates_by_ecosystem()

    for update in updates.values():
        assert update["schedule"]["interval"] == "weekly"

        limit = int(update["open-pull-requests-limit"])

        assert 1 <= limit <= 5


def test_dependabot_groups_minor_and_patch_but_not_major_updates() -> None:
    updates = updates_by_ecosystem()

    for update in updates.values():
        groups = update["groups"]

        assert set(groups) == {
            "minor-and-patch",
        }

        group = groups["minor-and-patch"]

        assert group["patterns"] == ["*"]
        assert set(group["update-types"]) == {
            "minor",
            "patch",
        }
        assert "major" not in group["update-types"]


def test_dependabot_commits_use_dependency_prefix() -> None:
    updates = updates_by_ecosystem()

    for update in updates.values():
        assert update["commit-message"]["prefix"] == "deps"


def test_dependabot_matches_repository_manifests() -> None:
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "uv.lock").is_file()

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "FROM " in dockerfile
    assert "image:" in compose
    assert "uses:" in workflow


def test_dependabot_configuration_contains_no_registry_credentials() -> None:
    text = DEPENDABOT_PATH.read_text(encoding="utf-8")

    assert "registries:" not in text
    assert "${{ secrets." not in text

    forbidden = (
        "sk_live_",
        "sk_test_",
        "whsec_",
        "AIza",
        "AKIA",
    )

    for marker in forbidden:
        assert marker not in text
