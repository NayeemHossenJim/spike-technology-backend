from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def test_alembic_has_exactly_one_migration_head() -> None:
    heads = ScriptDirectory.from_config(make_alembic_config()).get_heads()
    assert heads == ["0015_m7_ai_credit_adjustments"]


def test_alembic_path_separator_uses_current_setting() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert make_alembic_config().get_prepend_sys_paths_list() == ["."]


def test_offline_alembic_run_preserves_application_loggers() -> None:
    script = """
import io
import logging
from alembic import command
from alembic.config import Config

config = Config("alembic.ini", output_buffer=io.StringIO())
logger = logging.getLogger("app.services.email")
logger.disabled = False
command.upgrade(config, "head", sql=True)
raise SystemExit(1 if logger.disabled else 0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
