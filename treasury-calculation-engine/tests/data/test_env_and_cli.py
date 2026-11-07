"""Reading .env and the administrator command line."""

import json
from datetime import date

from hlag_engine.data import cli
from hlag_engine.data.env import read_env_file, read_env_value
from tests.data.conftest import FakeSource


def test_read_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nFRED_API_KEY="abc"\nexport OTHER = x\nEMPTY=\nbadline\n')
    assert read_env_file(env) == {"FRED_API_KEY": "abc", "OTHER": "x", "EMPTY": ""}
    assert read_env_file(tmp_path / "missing") == {}


def test_env_value_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FRED_API_KEY=from_file\nEMPTY=\n")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert read_env_value("FRED_API_KEY", env) == "from_file"
    monkeypatch.setenv("FRED_API_KEY", "from_env")
    assert read_env_value("FRED_API_KEY", env) == "from_env"
    assert read_env_value("EMPTY", env) is None


def test_env_example_and_gitignore():
    from hlag_engine.data.env import REPO_ROOT

    assert (REPO_ROOT / ".env.example").read_text().count("FRED_API_KEY=\n") == 1
    assert ".env" in (REPO_ROOT / ".gitignore").read_text().splitlines()


def test_cli_load_api(tmp_path, monkeypatch, capsys):
    source = FakeSource({"SOFR": [(date(2026, 10, 29), 3.94)]})
    monkeypatch.setattr(cli, "default_sources", lambda client: [source])
    db = tmp_path / "m.sqlite"
    assert cli.main(["load-api", "--db", str(db), "--as-of", "2026-10-30"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["series"]["SOFR"]["prints"] == 1 and out["series"]["SOFR"]["filled"] == 1


def test_cli_load_csv(tmp_path, capsys):
    good = tmp_path / "h.csv"
    good.write_text("series_id,date,value,unit,source_url,notes\nWCI_SHA_RTM,2026-01-08,2000,USD/FEU,https://x,\n")
    assert cli.main(["load-csv", "--db", str(tmp_path / "m.sqlite"), str(good)]) == 0
    bad = tmp_path / "bad.csv"
    bad.write_text("series_id,date,value,unit,source_url,notes\nWCI_SHA_RTM,2026-01-08,0,USD/FEU,https://x,\n")
    assert cli.main(["load-csv", "--db", str(tmp_path / "m.sqlite"), str(bad)]) == 1
    assert "non_positive_value" in capsys.readouterr().err
