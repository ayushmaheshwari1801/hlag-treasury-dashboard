"""The engine lives under one namespace, hlag_engine, and finds its config relative to the package (P88)."""

import fnmatch
import subprocess
import sys
import tomllib

from hlag_engine import PACKAGE_DIR, PROJECT_ROOT
from hlag_engine.company_profile.registers import REGISTER_DIR, load_registers
from hlag_engine.company_profile.scenario_validation import DEFAULT_THRESHOLDS_PATH, load_scenario_ranges
from hlag_engine.company_profile.schema import ACTIVE_SETTING_PATH, PROFILE_DIR, active_profile_path, load_profile
from hlag_engine.data.env import DEFAULT_ENV_PATH
from hlag_engine.engine.run import DEFAULT_STORE_PATH
from hlag_engine.engine.status import load_tier_b

SUBPACKAGES = ("contracts", "company_profile", "generator", "data", "modules", "engine")
LOCAL_ONLY = {PACKAGE_DIR / "data" / "history" / "weekly_history.csv"}  # raw Bloomberg data, git-ignored (P90)


def test_subpackages_sit_under_the_namespace():
    for name in SUBPACKAGES:
        assert (PACKAGE_DIR / name / "__init__.py").is_file()
        assert not (PROJECT_ROOT / name).exists(), f"stale top-level {name}/ left behind"


def test_config_and_data_paths_are_inside_the_package():
    assert PROFILE_DIR == PACKAGE_DIR / "company_profile"
    assert ACTIVE_SETTING_PATH == PROFILE_DIR / "active.yaml"
    assert REGISTER_DIR == PROFILE_DIR / "registers"
    assert DEFAULT_THRESHOLDS_PATH == PROFILE_DIR / "thresholds.yaml"
    assert DEFAULT_STORE_PATH == PACKAGE_DIR / "data" / "market.sqlite"
    assert DEFAULT_ENV_PATH == PROJECT_ROOT / ".env"


def test_config_loads_from_another_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert active_profile_path().parent == PROFILE_DIR
    assert load_profile().profile_version
    assert load_registers() is not None
    assert load_scenario_ranges() is not None
    assert load_tier_b() is not None


def test_cli_runs_as_a_namespaced_module_from_another_directory(tmp_path):
    env = {"PYTHONPATH": str(PROJECT_ROOT), "PATH": ""}
    out = subprocess.run([sys.executable, "-m", "hlag_engine.data.cli", "--help"], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=True)
    assert "python -m hlag_engine.data.cli" in out.stdout
    for command in ("load-api", "load-csv", "anchor-preview", "daily"):
        assert command in out.stdout


def test_package_data_ships_every_config_file():
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]
    assert config["packages"]["find"]["include"] == ["hlag_engine*"]
    globs = config["package-data"]
    for path in PACKAGE_DIR.rglob("*"):
        if path in LOCAL_ONLY or not path.is_file() or path.suffix in (".py", ".pyc", ".sqlite") or "__pycache__" in path.parts:
            continue
        package_dir = path.parent
        while not (package_dir / "__init__.py").exists():
            package_dir = package_dir.parent
        package = ".".join(package_dir.relative_to(PROJECT_ROOT).parts)
        relative = path.relative_to(package_dir).as_posix()
        assert any(fnmatch.fnmatch(relative, g) for g in globs.get(package, [])), f"{relative} not in {package} package-data"


def test_local_bloomberg_files_are_neither_shipped_nor_tracked():
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    ignored = (PROJECT_ROOT / ".gitignore").read_text().splitlines()
    for path in LOCAL_ONLY:
        relative = path.relative_to(PACKAGE_DIR / "data").as_posix()
        assert not any(fnmatch.fnmatch(relative, g) for g in config["hlag_engine.data"])
        assert path.relative_to(PROJECT_ROOT).as_posix() in ignored
    assert "docs/handoff/price_history_filled.xlsx" in ignored
