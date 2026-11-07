"""Closed value sets used across the contracts (spec sections 2, 4, 10)."""

from enum import StrEnum


class Currency(StrEnum):
    """Engine currencies (2.1). Signals may name any ISO 4217 code; see iso_codes."""

    USD = "USD"
    EUR = "EUR"
    CAD = "CAD"
    INR = "INR"
    BRL = "BRL"
    CNY = "CNY"
    GBP = "GBP"


class OwnerSpec(StrEnum):
    """Driver module that owns a line (2.1)."""

    FREIGHT = "freight"
    BUNKER = "bunker"
    RATES = "rates"
    FX = "fx"
    # Deviation from spec 2.1 (decisions_pending.md, "Direct cash flows"): dated USD cash amounts stated directly in
    # the scenario (e.g. a news Event's fine or one-off payment). Not a driver module; scenario pass only.
    DIRECT = "direct"


class BaselineTreatment(StrEnum):
    """How the baseline path uses a line code (2.2, D2)."""

    BASE_AMOUNT = "BASE_AMOUNT"
    DELTA_ONLY = "DELTA_ONLY"
    # BASE_AMOUNT when interest_in_ocf is false, DELTA_ONLY when true; the LEASE line
    # is DELTA_ONLY when lease repayments already include interest (D51).
    PER_INTEREST_IN_OCF = "PER_INTEREST_IN_OCF"
    # BASE_AMOUNT unless the capex baseline already includes it, then DELTA_ONLY (D34).
    PER_CAPEX_BASELINE = "PER_CAPEX_BASELINE"
    MEMO = "MEMO"  # never on the path (FX_POSITION)
    AGGREGATION_ONLY = "AGGREGATION_ONLY"  # computed, never stored


class LineCode(StrEnum):
    """The fixed line-code list (2.2)."""

    FREIGHT_REVENUE = "FREIGHT_REVENUE"
    FUEL_PURCHASE = "FUEL_PURCHASE"
    MFR_RECOVERY = "MFR_RECOVERY"
    INTEREST_EXPENSE = "INTEREST_EXPENSE"
    INTEREST_INCOME = "INTEREST_INCOME"
    REFINANCING_COST = "REFINANCING_COST"
    DEBT_PRINCIPAL = "DEBT_PRINCIPAL"
    DEBT_DRAWDOWN = "DEBT_DRAWDOWN"
    FX_OPERATING_COST = "FX_OPERATING_COST"
    DIVIDEND = "DIVIDEND"
    FX_TRANSACTION = "FX_TRANSACTION"
    FX_POSITION = "FX_POSITION"
    HEDGE_SETTLEMENT = "HEDGE_SETTLEMENT"
    MARGIN_CALL = "MARGIN_CALL"
    ACQUISITION_PAYMENT = "ACQUISITION_PAYMENT"  # ZIM purchase consideration at closing (P63)
    FX_CONVERSION_EFFECT = "FX_CONVERSION_EFFECT"
    EVENT_CASH_FLOW = "EVENT_CASH_FLOW"  # deviation from spec 2.2: a scenario's direct cash flow (owner direct)

    @property
    def baseline_treatment(self) -> BaselineTreatment:
        return LINE_CODE_TREATMENT[self]

    @property
    def owners(self) -> frozenset[OwnerSpec]:
        """Modules allowed to emit this code as a cash line (empty: never a cash line)."""
        return LINE_CODE_OWNERS[self]


_F, _B, _R, _X = OwnerSpec.FREIGHT, OwnerSpec.BUNKER, OwnerSpec.RATES, OwnerSpec.FX

LINE_CODE_OWNERS: dict[LineCode, frozenset[OwnerSpec]] = {
    LineCode.FREIGHT_REVENUE: frozenset({_F}),
    LineCode.FUEL_PURCHASE: frozenset({_B}),
    LineCode.MFR_RECOVERY: frozenset({_B}),
    LineCode.INTEREST_EXPENSE: frozenset({_R}),
    LineCode.INTEREST_INCOME: frozenset({_R}),
    LineCode.REFINANCING_COST: frozenset({_R}),
    LineCode.DEBT_PRINCIPAL: frozenset({_R, _X}),
    LineCode.DEBT_DRAWDOWN: frozenset({_R}),
    LineCode.FX_OPERATING_COST: frozenset({_X}),
    LineCode.DIVIDEND: frozenset({_X}),
    LineCode.FX_TRANSACTION: frozenset({_X}),
    LineCode.FX_POSITION: frozenset(),  # memo record, never a cash line (D19)
    LineCode.HEDGE_SETTLEMENT: frozenset({_B, _R, _X}),
    LineCode.MARGIN_CALL: frozenset({_B, _R}),
    LineCode.ACQUISITION_PAYMENT: frozenset({_R}),
    LineCode.FX_CONVERSION_EFFECT: frozenset(),  # engine aggregation, never stored
    LineCode.EVENT_CASH_FLOW: frozenset({OwnerSpec.DIRECT}),
}

LINE_CODE_TREATMENT: dict[LineCode, BaselineTreatment] = {
    LineCode.FREIGHT_REVENUE: BaselineTreatment.DELTA_ONLY,
    LineCode.FUEL_PURCHASE: BaselineTreatment.DELTA_ONLY,
    LineCode.MFR_RECOVERY: BaselineTreatment.DELTA_ONLY,
    LineCode.INTEREST_EXPENSE: BaselineTreatment.PER_INTEREST_IN_OCF,
    LineCode.INTEREST_INCOME: BaselineTreatment.PER_INTEREST_IN_OCF,
    LineCode.REFINANCING_COST: BaselineTreatment.PER_INTEREST_IN_OCF,
    LineCode.DEBT_PRINCIPAL: BaselineTreatment.BASE_AMOUNT,
    LineCode.DEBT_DRAWDOWN: BaselineTreatment.BASE_AMOUNT,
    LineCode.FX_OPERATING_COST: BaselineTreatment.DELTA_ONLY,
    LineCode.DIVIDEND: BaselineTreatment.BASE_AMOUNT,
    LineCode.FX_TRANSACTION: BaselineTreatment.PER_CAPEX_BASELINE,
    LineCode.FX_POSITION: BaselineTreatment.MEMO,
    LineCode.HEDGE_SETTLEMENT: BaselineTreatment.BASE_AMOUNT,
    LineCode.MARGIN_CALL: BaselineTreatment.BASE_AMOUNT,
    LineCode.ACQUISITION_PAYMENT: BaselineTreatment.BASE_AMOUNT,  # state, identical in both passes (P63)
    LineCode.FX_CONVERSION_EFFECT: BaselineTreatment.AGGREGATION_ONLY,
    # Emitted in the scenario pass only, so its base amount is zero and the whole amount is the scenario delta.
    LineCode.EVENT_CASH_FLOW: BaselineTreatment.BASE_AMOUNT,
}


class ProvenanceLabel(StrEnum):
    """Provenance labels (2.4). Exactly one per value."""

    ASSUMPTION = "ASSUMPTION"
    PROXY = "PROXY"
    DERIVED = "DERIVED"
    MARKET = "MARKET"
    DISCLOSED = "DISCLOSED"

    @property
    def rank(self) -> int:
        """Strength rank: ASSUMPTION (0) < PROXY < DERIVED < MARKET < DISCLOSED (4)."""
        return _LABEL_ORDER.index(self)

    @classmethod
    def weakest(cls, *labels: "ProvenanceLabel") -> "ProvenanceLabel":
        """Weakest-label rule (2.4, D41): a computed value takes its weakest input's label."""
        if not labels:
            raise ValueError("weakest() needs at least one label")
        return min(labels, key=lambda label: label.rank)


_LABEL_ORDER = [
    ProvenanceLabel.ASSUMPTION,
    ProvenanceLabel.PROXY,
    ProvenanceLabel.DERIVED,
    ProvenanceLabel.MARKET,
    ProvenanceLabel.DISCLOSED,
]


class PassType(StrEnum):
    BASE = "base"
    SCENARIO = "scenario"


class Trade(StrEnum):
    """Trades (2.6), shared by volume lines, freight shocks and signals."""

    ASIA_EUROPE = "ASIA_EUROPE"
    ASIA_AMERICA = "ASIA_AMERICA"
    EUROPE_AMERICA = "EUROPE_AMERICA"
    AFRICA_INTRA = "AFRICA_INTRA"


class Shape(StrEnum):
    """Scenario shapes (2.7)."""

    STEP = "step"
    RAMP = "ramp"
    SPIKE_REVERT = "spike_revert"


class ScenarioSource(StrEnum):
    PRESET = "preset"
    CUSTOM = "custom"
    AUTO_BUILT = "auto_built"
    SCHEDULED = "scheduled"


class RefinancingMode(StrEnum):
    ROLLOVER = "rollover"
    UNAVAILABLE = "unavailable"


class HedgeModule(StrEnum):
    """Modules that offer a hedge test (2.7); freight has none in v1 (6.7)."""

    BUNKER = "bunker"
    RATES = "rates"
    FX = "fx"


class HedgeInstrument(StrEnum):
    SWAP = "swap"
    FIXED_PRICE_SUPPLY = "fixed_price_supply"
    FORWARD = "forward"


class PresetScope(StrEnum):
    """Preset scope (2.7, D6). Sensitivity members are single-driver (D50)."""

    JOINT = "joint"
    FREIGHT = "freight"
    BUNKER = "bunker"
    RATES = "rates"
    FX = "fx"
    SENSITIVITY_RATES = "sensitivity_rates"
    SENSITIVITY_FX = "sensitivity_fx"

    @property
    def driver(self) -> OwnerSpec | None:
        """The single driver a scoped preset may set, or None for joint presets."""
        return {
            PresetScope.FREIGHT: OwnerSpec.FREIGHT,
            PresetScope.BUNKER: OwnerSpec.BUNKER,
            PresetScope.RATES: OwnerSpec.RATES,
            PresetScope.FX: OwnerSpec.FX,
            PresetScope.SENSITIVITY_RATES: OwnerSpec.RATES,
            PresetScope.SENSITIVITY_FX: OwnerSpec.FX,
        }.get(self)


class PresetId(StrEnum):
    """Permanent preset IDs (2.7, D62)."""

    RED_SEA_REESCALATION = "red_sea_reescalation"
    DEMAND_SLUMP = "demand_slump"
    OIL_SPIKE = "oil_spike"
    FED_CUTTING_CYCLE = "fed_cutting_cycle"
    EUR_RALLY = "eur_rally"
    EM_STRESS = "em_stress"
    FREIGHT_RATE_REVERSAL = "freight_rate_reversal"
    FED_CUTS_ECB_HOLDS = "fed_cuts_ecb_holds"
    BOTH_HIKING = "both_hiking"
    SPREAD_WIDENING = "spread_widening"
    CREDIT_SQUEEZE = "credit_squeeze"
    BROAD_USD_STRENGTHENING = "broad_usd_strengthening"
    CNY_DEVALUATION = "cny_devaluation"
    BUNKER_PRICE_REVERSAL = "bunker_price_reversal"
    HISTORY_REPLAY = "history_replay"
    RATES_UP_100BP = "rates_up_100bp"
    RATES_DOWN_100BP = "rates_down_100bp"
    FX_EUR_UP_10 = "fx_eur_up_10"
    FX_EUR_DOWN_10 = "fx_eur_down_10"
    FX_CAD_UP_10 = "fx_cad_up_10"
    FX_CAD_DOWN_10 = "fx_cad_down_10"
    FX_GBP_UP_10 = "fx_gbp_up_10"
    FX_GBP_DOWN_10 = "fx_gbp_down_10"
    FX_CNY_UP_10 = "fx_cny_up_10"
    FX_CNY_DOWN_10 = "fx_cny_down_10"
    FX_INR_UP_10 = "fx_inr_up_10"
    FX_INR_DOWN_10 = "fx_inr_down_10"
    FX_BRL_UP_10 = "fx_brl_up_10"
    FX_BRL_DOWN_10 = "fx_brl_down_10"

    @property
    def scope(self) -> PresetScope:
        return PRESET_SCOPES[self]


_JOINT = {
    PresetId.RED_SEA_REESCALATION,
    PresetId.DEMAND_SLUMP,
    PresetId.OIL_SPIKE,
    PresetId.FED_CUTTING_CYCLE,
    PresetId.EUR_RALLY,
    PresetId.EM_STRESS,
    PresetId.FREIGHT_RATE_REVERSAL,
}
_SCOPED = {
    PresetId.FED_CUTS_ECB_HOLDS: PresetScope.RATES,
    PresetId.BOTH_HIKING: PresetScope.RATES,
    PresetId.SPREAD_WIDENING: PresetScope.RATES,
    PresetId.CREDIT_SQUEEZE: PresetScope.RATES,
    PresetId.BROAD_USD_STRENGTHENING: PresetScope.FX,
    PresetId.CNY_DEVALUATION: PresetScope.FX,
    PresetId.BUNKER_PRICE_REVERSAL: PresetScope.BUNKER,
    PresetId.HISTORY_REPLAY: PresetScope.FREIGHT,
    PresetId.RATES_UP_100BP: PresetScope.SENSITIVITY_RATES,
    PresetId.RATES_DOWN_100BP: PresetScope.SENSITIVITY_RATES,
}
PRESET_SCOPES: dict[PresetId, PresetScope] = {
    p: (
        PresetScope.JOINT
        if p in _JOINT
        else _SCOPED.get(p, PresetScope.SENSITIVITY_FX)  # remaining IDs are fx_<ccy>_up/down_10
    )
    for p in PresetId
}


class EventType(StrEnum):
    """The single closed event-type list for the whole system (2.8, D42)."""

    CENTRAL_BANK_DECISION = "central_bank_decision"
    BANK_FAILURE = "bank_failure"
    CREDIT_EVENT = "credit_event"
    RATING_CHANGE = "rating_change"
    FINANCING_NEWS = "financing_news"
    REGULATION = "regulation"
    SANCTION = "sanction"
    TRADE_DISRUPTION = "trade_disruption"
    PORT_DISRUPTION = "port_disruption"
    COMMODITY_MOVE = "commodity_move"
    FX_POLICY = "fx_policy"
    CAPITAL_CONTROL = "capital_control"
    PAYMENT_RULE = "payment_rule"
    TAX_RULE = "tax_rule"
    LABOUR_ACTION = "labour_action"
    MARKET_STRUCTURE = "market_structure"
    CONTRACT_SEASON = "contract_season"
    COMPETITOR_ACTION = "competitor_action"
    COMPANY_ANNOUNCEMENT = "company_announcement"
    SUPPLIER_NEWS = "supplier_news"
    FLEET_CHANGE = "fleet_change"
    OTHER = "other"


class SignalKind(StrEnum):
    QUANTITATIVE = "quantitative"
    QUALITATIVE = "qualitative"
    FACTUAL = "factual"


class AffectedDriver(StrEnum):
    FREIGHT = "freight"
    BUNKER = "bunker"
    RATES = "rates"
    FX = "fx"
    LIQUIDITY = "liquidity"
    NONE = "none"


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    MIXED = "mixed"
    NONE = "none"


class MagnitudeUnit(StrEnum):
    PCT = "pct"  # whole percent: 25 means 25%
    BP = "bp"
    USD_PER_T = "usd_per_t"
    USD_PER_TEU = "usd_per_teu"
    OTHER = "other"


class AdjustmentStep(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class VerifierResult(StrEnum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    NOT_CHECKED = "not_checked"


class AdjustmentStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    WITHDRAWN = "withdrawn"


class AlertTier(StrEnum):
    """Alert tiers as written in outputs (4.8, D65)."""

    MARKET = "market"  # Tier A
    SCENARIO = "scenario"  # Tier B
    DESK_EXCEPTION = "desk_exception"


class AlertState(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLEARED = "cleared"


class AlertSeverity(StrEnum):
    """Tier A threshold level (4.5)."""

    WARNING = "warning"
    SEVERE = "severe"


class ScenarioStatus(StrEnum):
    """Tier B status (4.6)."""

    LOW = "LOW"
    WATCH = "WATCH"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    INCOMPLETE = "INCOMPLETE"


class ShortfallTier(StrEnum):
    """The four shortfall tiers (4.4, 4.8)."""

    WITHIN_OPERATING_CASH = "within_operating_cash"
    CREDIT_LINES_NEEDED = "credit_lines_needed"
    LIQUID_INVESTMENTS_NEEDED = "liquid_investments_needed"
    RESERVE_BREACHED = "reserve_breached"


class ReviewDecision(StrEnum):
    """Reviewer decision on a run (4.7)."""

    ACCEPTED = "accepted"
    OVERRIDDEN = "overridden"
    ESCALATED = "escalated"


class RecommendationLever(StrEnum):
    """Recommendation levers (10.4)."""

    HEDGE_RATIO_CHANGE = "hedge_ratio_change"
    HYPOTHETICAL_HEDGE = "hypothetical_hedge"
    LIQUIDITY_BUFFER = "liquidity_buffer"
    FACILITY_ACTION = "facility_action"
    PAYMENT_TIMING = "payment_timing"
    COLLECTIONS = "collections"
    DEPOSIT_TERM = "deposit_term"
    CONVERSION_TIMING = "conversion_timing"
    COMMERCIAL_REVIEW_FLAG = "commercial_review_flag"


class RecommendationStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class RecommendationTriggerKind(StrEnum):
    """What prompted a recommendation (10.4): an alert, a run or an event."""

    ALERT = "alert"
    RUN = "run"
    EVENT = "event"


class PlaybookTriggerType(StrEnum):
    """Playbook trigger kinds (5.1)."""

    ALERT_RULE = "alert_rule"
    STATUS = "status"
    PRESET_TYPE = "preset_type"


class NewsCategory(StrEnum):
    """Weekly report categories (10.5)."""

    REGULATORY = "regulatory"
    ECONOMIC = "economic"
    INDUSTRY = "industry"
