"""Active profile setting (P32): load_profile reads it; switching is a logged admin step."""

import shutil

import pytest

from hlag_engine.company_profile.schema import (
    ACTIVE_SETTING_PATH,
    DEFAULT_PROFILE_PATH,
    ProfileError,
    active_profile_path,
    load_profile,
)
from hlag_engine.data.profile_admin import ActivationError, activate_profile_version


@pytest.fixture
def profile_dir(tmp_path):
    (tmp_path / "active.yaml").write_text("profile_file: company_profile.yaml\n")
    shutil.copy(DEFAULT_PROFILE_PATH, tmp_path / "company_profile.yaml")
    text = DEFAULT_PROFILE_PATH.read_text().replace('profile_version: "2026H1-v1"', 'profile_version: "2026H1-v2"')
    (tmp_path / "company_profile_v2.yaml").write_text(text)
    return tmp_path


def test_repository_active_setting_names_v4():
    assert active_profile_path() == DEFAULT_PROFILE_PATH.parent / "company_profile_2026H1-v4.yaml"
    assert load_profile().profile_version == "2026H1-v4"
    assert ACTIVE_SETTING_PATH.read_text().startswith("# Active company profile version")


@pytest.mark.parametrize("content", ["profile_file: ../x.yaml\n", "profile_file: x.txt\n", "other: 1\n", ""])
def test_active_setting_must_name_a_plain_yaml_file(tmp_path, content):
    (tmp_path / "active.yaml").write_text(content)
    with pytest.raises(ProfileError):
        active_profile_path(tmp_path / "active.yaml")


def test_activate_switches_and_logs(store, profile_dir):
    setting = profile_dir / "active.yaml"
    result = activate_profile_version(store, "company_profile_v2.yaml", "admin", setting_path=setting)
    assert active_profile_path(setting) == profile_dir / "company_profile_v2.yaml"
    assert (result.previous_file, result.active_file, result.profile_version) == (
        "company_profile.yaml", "company_profile_v2.yaml", "2026H1-v2"
    )
    assert setting.read_text().startswith("# Active company profile version")
    [entry] = store.list_audit(action="profile_activated")
    assert entry.actor == "admin" and entry.details["previous_file"] == "company_profile.yaml"


def test_activate_guards(store, profile_dir):
    setting = profile_dir / "active.yaml"
    (profile_dir / "broken.yaml").write_text("profile_version: x\n")
    for name, match in [
        ("missing.yaml", "does not exist"),
        ("../company_profile.yaml", "plain"),
        ("company_profile.yaml", "already the active"),
    ]:
        with pytest.raises(ActivationError, match=match):
            activate_profile_version(store, name, "admin", setting_path=setting)
    with pytest.raises(Exception):
        activate_profile_version(store, "broken.yaml", "admin", setting_path=setting)
    assert active_profile_path(setting).name == "company_profile.yaml"  # unchanged
    assert store.list_audit() == []
