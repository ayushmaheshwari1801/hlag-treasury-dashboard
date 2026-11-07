"""Freight unit checks and the H1 2026 trade bridge (spec 6.13)."""

from dataclasses import dataclass

from hlag_engine.contracts.enums import Trade
from hlag_engine.modules.interface import ModuleInputError, ProfileContext

VOLUME_SUM_TOLERANCE = 0.001  # trade volumes sum to the group total within rounding (6,683 vs 6,684)


def check_trade_volumes(ctx: ProfileContext) -> None:
    """Trade volumes (TEU) must sum to the disclosed group volume (TEU); catches a TEU/TTEU mix-up."""
    group = float(ctx.resolve("freight.transport_volume_h1_teu"))
    trades = sum(float(ctx.resolve(f"freight.h1_volume_teu.{t.value}")) for t in Trade)
    if abs(trades / group - 1) > VOLUME_SUM_TOLERANCE:
        raise ModuleInputError(
            f"trade volumes sum to {trades:,.0f} against group volume {group:,.0f} TEU: "
            "check units (TEU vs TTEU)"
        )


@dataclass(frozen=True)
class BridgeResult:
    volume_effect: float  # sum of dV x prior rate
    rate_effect: float  # sum of dP x current volume
    reported_change: float
    residual: float


def trade_bridge(current: dict[str, tuple[float, float]], prior: dict[str, tuple[float, float]],
                 reported_change: float) -> BridgeResult:
    """Inputs per trade: (volume in TTEU, rate in USD/TEU). Effects in USD m (TTEU x USD/TEU / 1,000)."""
    volume = sum((current[t][0] - prior[t][0]) * prior[t][1] for t in current) / 1000
    rate = sum((current[t][1] - prior[t][1]) * current[t][0] for t in current) / 1000
    return BridgeResult(volume, rate, reported_change, reported_change - (volume + rate))
