"""Contracts (spec section 2): the only definition of every shape passed between components."""

from hlag_engine.contracts._base import FieldCategory
from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.alert import Alert, AlertExplanation, AlertInput
from hlag_engine.contracts.alert_rule import AlertRule, AlertRuleType, AlertThresholds, OnSevere, ThresholdLevel
from hlag_engine.contracts.auto_build import AutoBuildRequest
from hlag_engine.contracts.baseline_change import BaselineChange, BaselineChangeWindow, DriverChange
from hlag_engine.contracts.cash_line import CashLine, round_to_cents
from hlag_engine.contracts.enums import *  # noqa: F403
from hlag_engine.contracts.hedge_test import HedgeTest
from hlag_engine.contracts.metric import MetricResult
from hlag_engine.contracts.pair_result import DatedAmount, HedgeMetrics, MarginTest, PairDifference, PairResult
from hlag_engine.contracts.playbook import PlaybookEntry, PlaybookEntryId
from hlag_engine.contracts.policy_hedge import CAD_RATIO_CAP, HedgePolicy, PolicyHedgeSpec
from hlag_engine.contracts.recommendation import (
    EvidenceRuns,
    ExpectedEffect,
    Recommendation,
    RecommendationTrigger,
    RisksAndLimits,
)
from hlag_engine.contracts.run_record import MarketSnapshotEntry, RunRecord, RunReview
from hlag_engine.contracts.run_summary import Attribution, DeskException, DeskExceptionKind, Headroom, RunFlags, RunSummary, ScenarioRef
from hlag_engine.contracts.scenario import (
    BunkerParams,
    FreightParams,
    FxParams,
    HistoryReplay,
    RateStep,
    RatesParams,
    ScenarioParams,
    WeeklyShock,
)
from hlag_engine.contracts.signal import RejectedSignal, Signal, SubmitSignalResponse, submit_signal
from hlag_engine.contracts.volume_line import VolumeLine
from hlag_engine.contracts.weekly_report import WeeklyReport, WeeklyReportItem, WeeklyReportSection
