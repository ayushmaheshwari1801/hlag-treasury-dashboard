"""Shared fixtures. The complete profile fills every placeholder with round test numbers,
so tests never depend on values still to be entered (these are not real figures)."""

import copy
from datetime import date

import pytest

from hlag_engine.company_profile.registers import load_registers
from hlag_engine.company_profile.schema import DEFAULT_PROFILE_PATH, PROFILE_DIR, load_raw, profile_from_dict

T0 = date(2026, 10, 31)

FIXTURE_VALUES = {
    "liquidity.annual_lease_repayments_usd": 1_200_000_000,
    "liquidity.capex_monthly_usd": 100_000_000,
    "debt.current_lease_liabilities_usd": 652_000_000,  # 12-month maturities = USD 1,200m
    "fx.usd_per_eur_close_2025_12_31": 1.2,
    "fx.usd_per_eur_close_2026_06_30": 1.25,
    "freight.group_volume_teu.2025Q3": 3_000_000,
    "freight.group_volume_teu.2025Q4": 3_100_000,
    "freight.group_volume_teu.2026Q1": 3_200_000,
    "freight.group_volume_teu.2026Q2": 3_484_000,
    "fx.dividend_reference_eur": 500_000_000,
    "rates.fomc_meeting_dates": ["2026-12-09", "2027-01-27", "2027-03-17"],
    "rates.ecb_meeting_dates": ["2026-12-17", "2027-02-04", "2027-03-18"],
    "fx.lock_rate_usd_per_unit.INR": 0.012,
    "fx.lock_rate_usd_per_unit.BRL": 0.18,
}
ANCHOR_FIXTURE = 100.0


def complete_raw(path=None) -> dict:
    """The active profile (or the file at path) with every placeholder filled by FIXTURE_VALUES."""
    raw = copy.deepcopy(load_raw(path))
    params = raw["parameters"]
    for key, value in FIXTURE_VALUES.items():
        params[key]["value"] = value
        params[key]["status"] = "ok"
    for series in raw["anchor_snapshot"]["series"].values():
        if series.get("value") is None:
            series["value"] = ANCHOR_FIXTURE
            series["status"] = "ok"
    return raw


@pytest.fixture(scope="session")
def raw_profile() -> dict:
    return copy.deepcopy(load_raw())


@pytest.fixture(scope="session")
def real_profile():
    return profile_from_dict(load_raw())


@pytest.fixture(scope="session")
def v1_profile():
    """Profile version 2026H1-v1 (section 12 defaults and the original placeholders), no longer active."""
    return profile_from_dict(load_raw(DEFAULT_PROFILE_PATH))


@pytest.fixture(scope="session")
def v2_profile():
    """Profile version 2026H1-v2: the 20 pending-history items are still unfilled (active 2-3 Nov 2026)."""
    return profile_from_dict(load_raw(PROFILE_DIR / "company_profile_2026H1-v2.yaml"))


@pytest.fixture(scope="session")
def profile():
    return profile_from_dict(complete_raw())


@pytest.fixture(scope="session")
def registers():
    return load_registers()
