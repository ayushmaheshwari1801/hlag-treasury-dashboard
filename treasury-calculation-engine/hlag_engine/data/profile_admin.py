"""Switching the active profile version (spec 3.1), P32.

An explicit administrator step, written to the audit log. Writing a new version
(hlag_engine.data.anchor.write_anchor_profile_version) never switches it.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from hlag_engine.company_profile.schema import ACTIVE_SETTING_PATH, active_profile_path, load_profile
from hlag_engine.data.store import Store

SETTING_HEADER = """# Active company profile version (spec 3.1, decision P32). load_profile() with no path reads
# the file named here. Change it only with hlag_engine.data.profile_admin.activate_profile_version, an
# explicit administrator step that is written to the audit log; never edit it by hand.
"""


class ActivationError(Exception):
    pass


@dataclass
class ActivationResult:
    previous_file: str
    active_file: str
    profile_version: str
    content_hash: str


def activate_profile_version(
    store: Store, file_name: str, actor: str, setting_path: Path | str = ACTIVE_SETTING_PATH
) -> ActivationResult:
    setting_path = Path(setting_path)
    if Path(file_name).name != file_name or not file_name.endswith(".yaml"):
        raise ActivationError(f"file_name must be a plain .yaml file name in {setting_path.parent}, got {file_name!r}")
    target = setting_path.parent / file_name
    if not target.exists():
        raise ActivationError(f"{target} does not exist")
    previous = active_profile_path(setting_path)
    if previous.name == file_name:
        raise ActivationError(f"{file_name} is already the active profile")
    profile = load_profile(target)  # must validate before it can become active
    setting_path.write_text(SETTING_HEADER + f"profile_file: {file_name}\n", encoding="utf-8")
    result = ActivationResult(previous.name, file_name, profile.profile_version, profile.content_hash)
    store.append_audit(actor, "profile_activated", profile.profile_version, asdict(result))
    return result
