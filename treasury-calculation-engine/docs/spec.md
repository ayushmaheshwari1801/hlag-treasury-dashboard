
# Treasury Risk Engine: Combined Prototype Specification

Oct 31, 2026 · @ayush


## 1. Overview

The prototype is a deterministic cash-flow stress engine for Hapag-Lloyd's treasury. Four driver modules (freight, bunker, interest rates, FX) turn market moves into dated cash lines; a shared engine nets them into one 12-month liquidity path; agents read news, propose scenarios and parameter updates, and recommend treasury actions for a human to approve. Nothing is ever executed.

This document is written for an AI coding agent. Statements using MUST are requirements; SHOULD marks a default that may change with a recorded reason. Every number carries a provenance label (section 2.4).


### 1.1 What the prototype delivers against the brief


| Brief deliverable | Where it is built | Spec section |
| :--- | :--- | :--- |
| Alerts on time-sensitive events and threshold breaches | Tier A market alerts, Tier B scenario status, news-engine events | 4 (engine), 10 (agents) |
| Dashboard of risks and opportunities | Streamlit dashboard over run records and market data | 11 |
| Conversational AI for hypotheticals and sensitivity analysis | Chat agent calling run_scenario and sliders (run overrides) | 10, 11 |
| Agentic AI pulling internal and external data | Data agents (WCI, bunker prices), adjustment agent, reference registers | 3, 10 |
| Proactive policy recommendations | Recommendation agent using the hypothetical hedge tools and module playbooks | 6–10 |
| Weekly treasury brief (what changed, why it matters, what to do) | Brief agent built from news events and run records | 10 |



### 1.2 Reference company and scope

The reference company is Hapag-Lloyd AG. Its functional currency is USD and its reporting currency is EUR (AR 2025, p. 215). It is rated BB+ (S&P) and Ba1 (Moody's) (AR 2025, p. 35). All company data comes from the Annual Report 2025 (AR) and the Investor Report H1 2026 (IR); there is no internal data. Where data is missing, the exposure generator builds a labelled synthetic register sized to the disclosures.

In scope: Liner Shipping freight revenue; bunker purchases and MFR (Marine Fuel Recovery) recovery; floating-rate interest, interest income and refinancing cost; FX conversion of all non-USD cash and FX-owned exposures (EUR, CAD, INR, BRL, CNY, GBP); group liquidity over 12 months from the as-of date.

Out of scope, shown as dashboard notes only: CO2 / EU ETS certificate cost; demurrage and detention revenue; the Terminal & Infrastructure segment; special fund fair-value effects; covenant headroom; capital controls and repatriation rules (qualitative, through the news engine); EUR translation of the accounts; feedback loops between drivers; forward points.

The engine never executes trades, hedges or payments, and never claims to show the live company position.


### 1.3 Design principles

One question per module. Each module answers only: what does my driver do to cash, line by line? Netting, conversion, liquidity, status and attribution live in the shared engine.

Gross native lines. Modules emit signed cash lines in their native currency and never net or convert them.

Baseline plus deltas. A simple baseline path carries the company's normal cash flow. Modules emit full gross amounts for every line in both the baseline and scenario passes. The line code's baseline treatment (2.2) decides only what the baseline path adds: BASE_AMOUNT lines add their base-pass USD amount; DELTA_ONLY lines contribute only their scenario delta, because their base amount is already inside the OCF run-rate.

Deterministic engine, reasoning agents. run_scenario is plain Python with no LLM inside. Agents decide what to run, what news means and what to recommend.

Three layers of parameter values. Company profile, then approved active adjustments, then run overrides (section 3.2).

Honest provenance. Every value has exactly one label, and assumptions never read as evidence.

Human approval. Agents never change the baseline on their own. Every adjustment and recommendation needs a human decision.

Reproducible runs. Any run can be rebuilt from its run record: profile version, active adjustments, overrides and market data as-of dates.


### 1.4 Components

Contracts: Pydantic models for cash lines, volume lines, scenario parameters, signals, adjustments and run records (section 2).

Shared data: company profile, adjustment layer and catalogue, reference registers, exposure generator, market data store, baseline path (section 3).

Shared engine: orchestrator, FX conversion layer, attribution, liquidity tests, alerts, run record, agent tools (section 4; the conversion layer in 9A).

Driver modules: freight, bunker, rates, FX, each following one template (sections 5–9).

Agents: data retrieval, signal router, scenario builder, adjustment, verifier, recommendation, alert investigator, chat and weekly brief (section 10).

Dashboard: Streamlit app with market view, scenario runner, liquidity path, attribution, alerts, approval queue and active adjustments (section 11).

News engine: built separately. Its only contract with this engine is the signal schema (section 2.8).

one run · parameter layers to liquidity path

Parameters flow top to bottom inside one deterministic run; agents sit outside it and change only the adjustment and override layers, through human approval or a labelled scenario.


### 1.5 Technology stack


| Layer | Choice | Notes |
| :--- | :--- | :--- |
| Language | Python 3.11+ | Type hints throughout |
| Contracts | Pydantic v2 | One model per contract in contracts/ |
| Storage | SQLite (one file) | Tables: market_data, run_records, cash_lines, adjustments, confirm_queue, audit_log, signals, auto_build_queue, baseline_changes |
| Numerics | pandas, numpy | No randomness anywhere in the engine or generator |
| HTTP | httpx | Connectors behind one MarketDataSource interface |
| Config | YAML (company_profile.yaml, thresholds.yaml, presets.json) | Loaded and validated at start-up |
| Dashboard | Streamlit | Decided by the team (D56); one Python app over the engine's Python functions |
| Agents | Anthropic Claude API with tool use | ASSUMPTION: no stack was specified |
| Tests | pytest | Acceptance tests in section 14 |


The engine is reached only through Python functions (run_scenario, get_run and the other agent tools in section 4), optionally wrapped in a thin FastAPI layer. The choice of front end MUST NOT affect the engine.

Suggested repository layout: contracts/, profile/, data/ (connectors, store, retrieval), generator/, modules/ (freight, bunker, rates, fx), engine/ (orchestrator, conversion, liquidity, alerts, attribution), agents/, dashboard/, tests/.

The earlier code (dashboard_finance.py 91-day liquidity engine and the ECB/€STR connector) was not supplied with this spec. The agent MUST build these components from this specification; if the code is later provided, it MAY be reused where it matches the contracts here.


### 1.6 Document precedence and decision log

The agreed change logs, the shared-layer plan and the adjustment design override the four original specs wherever they conflict. The originals are used only for domain detail the change logs keep: evidence references, back-test figures and boundary rules. The decisions below resolve the remaining conflicts and gaps found when the documents were combined.


| ID | Decision | Resolves |
| :--- | :--- | :--- |
| D1 | Maturing financial debt is refinanced by default (refinancing_mode = rollover): repayment and new drawdown on the same date, cash-neutral at base FX. A scenario may set refinancing_mode = unavailable. Leases are excluded from maturities and enter only through the lease payment line. | Double count of leases; repay-vs-refinance contradiction |
| D2 | Each line code has one baseline treatment: BASE_AMOUNT (financing items added to the baseline path) or DELTA_ONLY (operating items already inside the OCF run-rate). | Dividend and principal counted twice |
| D3 | The DSO default is labelled DERIVED. | Freight A5 vs shared label table |
| D4 | A run whose material lines rely on PROXY prices cannot report LOW and shows a "proxy-based" flag. | Rule dropped from the shared plan |
| D5 | Scenario shape is set per driver, defaulting to a common value. Rates use dated steps instead of a shape. | Common vs per-driver shape |
| D6 | One preset library: joint presets, module presets and a sensitivity family (D50), each tagged by scope. The freight preset is named "freight rate reversal". | Mismatched preset lists; name clash |
| D7 | Contract leakage triggers and shares are profile parameters that a scenario may override. | Two homes for one parameter |
| D8 | Agent-retrieved values that pass validation are usable at once, flagged unconfirmed. Only failed checks go to the confirm queue. | Pending-until-confirmed vs confirm-if-inconsistent |
| D9 | Signals route three ways: scenario, adjustment proposal, recommendation only. | Factual signals bypassing the adjustment agent |
| D10 | The ±100 bp check against AR p. 217 is a static full-year calculation, separate from the cash engine. | Size test the cash engine cannot pass |
| D11 | The FX ±10% check moves USD per foreign unit by −10% and +10%. | 9.09% vs 10% ambiguity |
| D12 | Each EUR disclosure is converted with the closing USD/EUR rate of its own reporting date, held in the profile as DISCLOSED. | Two different closing rates cited to IR p. 16 |
| D13 | Tier B status LOW is below 10% of headroom consumed. The desk exception threshold is a separate 25% of headroom, gross, per driver, decided by the team (D55). | Desk exception almost never firing |
| D14 | Tier A thresholds get numeric defaults in thresholds.yaml. | No numbers given |
| D15 | A VolumeLine contract carries TEU from freight to bunker through the orchestrator. | No volume field in cash lines |
| D16 | MFR cash arrives after the freight collection lag (DSO). | MFR timing undefined |
| D17 | Lines are snapped to month-end before pairing, and pairing is a full outer join with missing amounts set to zero. | Transit delays shifting dates |
| D18 | FX policy hedges are sized on their own exposure; netting applies to the unhedged remainder. EUR forwards are struck at base spot. | Net vs gross hedge sizing |
| D19 | CNY and GBP positions are balance-sheet translation exposure: a memo item in the FX panel, used for the sign check, never on the liquidity path. | Stock positions in a flow ledger |
| D20 | A tested hypothetical hedge applies in both passes. The rates swap tool prices at the 2-year yield plus a spread (ASSUMPTION). | Hedge effects counted as deltas |
| D21 | The special fund (USD 2,035m) is a separate liquidity tier, counted in headroom only when switched on (default off). | Disclosed liquid assets missing from tiers |
| D22 | Liquidity tiers are split by counterparty so an adjustment can remove one bank's share. | Bank failure adjustments |
| D23 | Lease interest is emitted as a fixed INTEREST_EXPENSE line with sub_id LEASE. | Rates calibration includes lease interest |
| D24 | The ZIM tranche is interest-only: drawdown and acquisition outflow net to zero in v1. | Liquidity effect of the acquisition |
| D25 | Statistically estimated parameters (such as bunker β) are labelled MARKET with the formula recorded. | No label for estimates |
| D26 | The exposure generator has no randomness, so worked examples reproduce exactly. | Reproducible acceptance tests |
| D27 | The brief's profile inputs (payment rails, cash structure, counterparty locations, operating countries) live in the reference registers and feed news relevance only. | Brief items missing from the design |
| D28 | Every scenario field is either shock (reset by zero_shock) or state (identical in both passes). State effects are shown by comparing two runs. | ZIM or hedge tests appearing as scenario deltas |
| D29 | Current debt maturities are spread evenly from 1 Jul 2026 to 30 Jun 2027; later horizon months continue at the same monthly amount (ASSUMPTION). | No maturities for Jul–Oct 2027 |
| D30 | Removing a counterparty's deposits cuts T1 once, by its share on the effective date; later flows land with the remaining counterparties. | Unclear effect on future flows |
| D31 | Plausibility bands are set per series (fuel ±40%, WCI ±80%), and a queued value raises a "possible major move" alert. | Real crisis moves stuck in the confirm queue |
| D32 | A run using unconfirmed retrieved data carries a run-level flag. | Unconfirmed data invisible in results |
| D33 | Calculations may use float64 if both passes run identical code paths; amounts are rounded to cents when stored. | Decimal clashing with pandas |
| D34 | The capex baseline excludes FX_TRANSACTION amounts; if its source already includes them, FX_TRANSACTION is DELTA_ONLY. | INR and BRL obligations counted twice |
| D35 | Freight P0 and the bunker MFR base reflect the anchor period (H1 2026). Index and price moves since then are measured from the anchor snapshot and reach the baseline through the anchor adjustment (D69), never through P0. Price paths stay flat from T0 in the base pass. | A disclosed all-in rate level cannot be reconciled with index history before T0 |
| D36 | The Red Sea transit delay defaults to 12 days (the change log gave 10–14). | A range is not a parameter value |
| D37 | History replay and reversal presets compute their shocks from history when built and store explicit numbers (freight rate_shock_path), so replay never needs history. | Replay depending on market history |
| D38 | The bunker base price is the last weekly print (MARKET). Brent-filled prices are dashboard indicators, used by the engine only when a print is stale, which sets the proxy-based flag. | Every bunker run being proxy-based under D4 |
| D39 | The index-linked clause pass-through share is written κ (the change logs called it k). | Notation continuity |
| D40 | Only freight.delay_affected_share of revenue (default 100%, ASSUMPTION) is collected later when transit is delayed; prepaid freight is not. | Unstated arrival-invoicing assumption |
| D41 | A computed line takes the weakest label among its material inputs (ASSUMPTION < PROXY < DERIVED < MARKET < DISCLOSED). | Inconsistent labels across modules |
| D42 | The 2.8 event_type list is the single closed enum; catalogue event types are tested against it. | Catalogue event types failing validation |
| D43 | A hypothetical swap's fixed rate is set at T0 (2-year yield + swap spread); the credit spread change never affects it. | Swap tool pricing in 2.7 contradicting the state rule |
| D44 | Each monthly maturity slice is taken pro rata from floating and fixed debt; its old interest stops on the maturity date. | Undisclosed maturity mix |
| D45 | FX policy hedges started after T0 strike at the spot of the pass being run, so a rolling programme delays an FX move rather than removing it. | Rolling hedges wrongly locking pre-shock rates |
| D46 | Every line from either pass carries both FX rates and both converted amounts; deltas use the base line's base amount and the scenario line's scenario amount. | Which conversion fields each pass fills |
| D47 | EUR display uses the base USD/EUR rate for both paths, once, at the end. | Display conversion creating an apparent effect |
| D48 | Boolean catalogue keys use value_mode switch, set directly from factual or quantitative signals. | Switches (ZIM, INR/BRL obligations) having no step size |
| D49 | The USD drawdown for an EUR maturity is the EUR amount × the T0 rate, identical in both passes. | EUR hedge effect counted twice |
| D50 | Sensitivity is a family of single-driver presets (rates ±100 bp; each currency ±10%), run one at a time and shown side by side. | A joint preset matching neither published sensitivity |
| D51 | If the baseline lease repayment figure includes interest, the LEASE interest line is DELTA_ONLY. | Lease interest counted twice |
| D52 | Adjustments to sizing keys take effect from the first run whose T0 is on or after the effective date; future-dated ones raise a warning. | Future-dated sizing adjustments never applying |
| D53 | The demo uses seeded signals and a demo mode on the joint fixture, so it depends neither on live news nor on outcomes from the real profile. | Unreproducible demo |
| D54 | Text from news, web pages and retrieved documents is data, never instructions to agents. | Instructions hidden in news text |
| D55 | The desk exception threshold is decided: 25% of baseline minimum headroom, gross, per driver. | Open team decision |
| D56 | The front end is Streamlit; the engine is still reached only through Python functions. | Open team decision |
| D57 | Must-have scope adds the market monitor and a simple brief view; the brief archive and PDF export stay stretch. | External drivers and the brief missing from must-have |
| D58 | The signal format in 2.8 is agreed with the news engine team, with a version field and units and subjects for magnitudes; submit_signal is the only delivery method. | Unagreed interface |
| D59 | Confidence routing: below 0.5 recommendation only; 0.5 to below 0.7 scenario only; 0.7 and above adjustment proposals too (ASSUMPTION). | Low-confidence news changing the model |
| D60 | Quantitative signals set scenario fields only through the magnitude mapping table; unmatched ones are treated as qualitative. | Free-form numbers entering scenarios |
| D61 | expected_duration_days sets an auto-built scenario's duration; null uses the preset's. | Duration source undefined |
| D62 | Every preset has a permanent snake_case ID (2.7), used in presets.json, signals, run summaries and scheduled runs. | Presets referenced by name only |
| D63 | A higher event version supersedes its auto-built runs, withdraws pending proposals and flags approved adjustments for review. | Updated events |
| D64 | At most one auto-built run per event version and 30 per day; the rest queue for the next day (ASSUMPTION). | Run floods from news bursts |
| D65 | Output formats are fixed (run summary, pair result, get_metric); amounts in USD m; alert tiers written market, scenario, desk_exception. | Undefined outputs for the recommendation engine |
| D66 | A deterministic scheduled job runs the baseline and a standard preset set after each daily data load. | Stale results in quiet weeks |
| D67 | The baseline change is measured on a market-adjusted path, swapping driver groups in a fixed order so the parts sum exactly. Superseded by D71 once the anchor adjustment (D69) made the baseline reflect market prices. | Operating market moves never reach the flat baseline path |
| D68 | The news engine's weekly report, if provided, feeds "What changed"; otherwise the brief builds it from that week's signals. | Two competing weekly briefs |
| D69 | The baseline carries an anchor adjustment: the fixed-order swap applied from an H1 2026 anchor snapshot to T0 prices, on DELTA_ONLY lines only, split by driver in the fixed order and shown as its own component. | OCF run-rate embedding H1 2026 market conditions |
| D70 | The anchor snapshot is stored in the profile and replaced together with the OCF run-rate and P0 whenever a newer report is entered; the anchor period is always the run-rate's period. | Anchor drifting from the run-rate it corrects |
| D71 | The weekly baseline change is the difference between two anchored baselines: other changes from rolling T0 at last week's prices, then one driver group swapped to today's prices at a time, recomputing the anchored baseline each step. | Market-adjusted path redundant once the baseline carries the anchor adjustment |



## 2. Contracts

Seven Pydantic models define everything that passes between components: cash line, volume line, scenario parameter set, hedge test, signal, adjustment and run record (run record in section 4). Each model MUST live in contracts/ and be the only definition of its shape.


### 2.1 Cash line

Every module emits cash lines only, in native currency. The FX conversion layer fills the conversion fields later.


| Field | Type | Meaning | Filled by |
| :--- | :--- | :--- | :--- |
| sub_id | str | Sub-item identifier: contract cohort, debt tranche, exposure ID, counterparty | Module |
| date | date | Calendar date of the cash movement (snapped per 2.5) | Module, then orchestrator |
| currency | enum | USD, EUR, CAD, INR, BRL, CNY, GBP | Module |
| amount | Decimal | Signed native amount: cash in positive, cash out negative | Module |
| owner_spec | enum | freight, bunker, rates, fx | Module |
| line_code | enum | From the fixed list in 2.2 | Module |
| label | enum | Provenance label of the amount (2.4) | Module |
| pass_type | enum | base or scenario | Orchestrator |
| event_ids | list[str] | News events behind the scenario, if any | Orchestrator |
| run_id, scenario_id | str | Which run produced the line | Orchestrator |
| entity, parent_id | str or None | Reserved; one entity (HLAG_GROUP) and no multi-leg instruments in v1 | Module |
| fx_rate_base, fx_rate_scenario | Decimal or None | USD per one unit of currency; None until converted; 1.0 for USD lines | FX conversion layer |
| amount_usd_base, amount_usd_scenario | Decimal or None | Converted amounts | FX conversion layer |


The stable line key is (owner_spec, line_code, date, currency, sub_id). Base and scenario lines are paired on this key after date snapping, with a full outer join; a line missing on one side counts as zero there (D17).

Calculations MAY use float64, as long as both passes run identical code paths. Amounts are rounded to cents only when stored. The zero-shock test requires delta == 0 exactly (D33).


### 2.2 Line codes and baseline treatment

Modules emit gross lines only, at full amounts in both passes; nothing is netted inside a module, and the treatment column decides only how the baseline path uses each line (D2). BASE_AMOUNT lines are not in the OCF run-rate, so the baseline path adds their base-pass USD amount. DELTA_ONLY lines are already inside the OCF run-rate, so only the scenario delta reaches the path.


| Line code | Owner | What it is | Baseline treatment |
| :--- | :--- | :--- | :--- |
| FREIGHT_REVENUE | freight | Collected freight revenue by trade, volume class and currency | DELTA_ONLY |
| FUEL_PURCHASE | bunker | Fuel purchase payments | DELTA_ONLY |
| MFR_RECOVERY | bunker | Change in MFR revenue collected, scenario-driven (zero with no shock) | DELTA_ONLY |
| INTEREST_EXPENSE | rates | Interest on floating debt, fixed debt, leases (sub_id LEASE) and the ZIM tranche | Per interest_in_ocf flag: false → BASE_AMOUNT, true → DELTA_ONLY; the LEASE line is DELTA_ONLY when the baseline lease repayment figure already includes interest (D51) |
| INTEREST_INCOME | rates | Interest on interest-bearing cash | Same flag as above |
| REFINANCING_COST | rates | Interest on refinanced debt at the new-issue rate | Same flag as above |
| DEBT_PRINCIPAL | rates (USD), fx (EUR) | Repayment of maturing financial debt, excluding leases | BASE_AMOUNT |
| DEBT_DRAWDOWN | rates | New USD borrowing replacing maturing debt, USD even for EUR maturities (ASSUMPTION, consistent with the disclosed hedging of EUR debt repayment), when refinancing_mode = rollover | BASE_AMOUNT |
| FX_OPERATING_COST | fx | EUR and CAD operating costs | DELTA_ONLY |
| DIVIDEND | fx | EUR dividend payment | BASE_AMOUNT |
| FX_TRANSACTION | fx | INR and BRL purchase-price obligations | BASE_AMOUNT, unless the capex baseline already includes it, then DELTA_ONLY (D34; open item) |
| FX_POSITION | fx | Memo record type for CNY and GBP translation exposure (FX panel and sign check, D19); never emitted as a cash line | Memo, not on path |
| HEDGE_SETTLEMENT | bunker, rates, fx | Settlement of a hypothetical hedge or an FX policy hedge | BASE_AMOUNT |
| MARGIN_CALL | bunker, rates | Margin cash on a tested hypothetical swap | BASE_AMOUNT (and the margin-eligible test) |
| FX_CONVERSION_EFFECT | engine | Aggregation only, computed, never stored | n/a |


DEBT_PRINCIPAL and DEBT_DRAWDOWN are the only additions to the shared plan's code list (D1). With rollover, a USD maturity nets to zero at every FX rate; a EUR maturity nets to zero at base FX but carries FX exposure, which matches the disclosed policy of hedging EUR debt repayment.

The default of interest_in_ocf is false, labelled ASSUMPTION, pending the cash flow statement check (open item).


### 2.3 Sign convention and deltas

Cash in is positive and cash out is negative. An adverse move is any move that reduces cash. This replaces every module-specific convention in the original specs ("positive = adverse", "rate decrease is adverse").

For each paired line: native delta = scenario amount − base amount. A positive delta is favourable, a negative delta adverse. Every module runs both directions of every shock; upside is reported as opportunity, never suppressed.


### 2.4 Provenance labels

Each value carries exactly one label. Combined labels such as "ASSUMPTION / DERIVED" are not allowed, and a rationale MUST never sound evidence-based unless it is.


| Label | Meaning | Example |
| :--- | :--- | :--- |
| DISCLOSED | Straight from a report or official publication, with source and page | H1 2026 consumption 2.4m t (IR pp. 10–11) |
| DERIVED | Calculated from disclosures, formula recorded | DSO ≈ 48–50 days; fuel per TEU ≈ 0.36 t |
| MARKET | From an API, a confirmed or validated agent retrieval, or a statistic estimated from such data (formula recorded) | WCI, SOFR, ECB FX, bunker β |
| PROXY | Market data standing in for something unobserved | Brent-filled daily fuel prices; WCI composite for Africa & Intraregional |
| ASSUMPTION | A chosen parameter, with default, allowed range and honest rationale | MFR share, hedge ratios |


Whether a retrieved value is confirmed is a separate boolean (confirmed), not a label (D8). Synthetic register items take the label of the figure that sized them and carry synthetic = true.

Weakest-label rule. A computed line takes the weakest label among its material inputs, ranked ASSUMPTION < PROXY < DERIVED < MARKET < DISCLOSED. For example, a fuel purchase line computed from market prices, assumed hub weights and an assumed scrubber share is labelled ASSUMPTION. The label describes the line; the dashboard can still show the market inputs behind it with their own labels.

Threshold settings (alert and status levels) are ASSUMPTION values that carry the display tag PROPOSED ILLUSTRATIVE SETTINGS. The tag is not a sixth provenance label.


### 2.5 Timeline

T0 is the run's as-of date. The horizon runs from T0 + 1 day to the last day of the month containing T0 + 12 months.

Modules emit exact calendar dates.

Days 1–91 (T0 + 1 to T0 + 91): the orchestrator keeps daily detail.

After day 91: every line snaps to the last day of its calendar month, before pairing (D17).

Lines dated after the horizon are dropped from the path. The rates refinancing watch list (months 12–24) is a separate list, never cash on the path.

Documented limitation: after day 91, a mid-month cash low point is hidden, so the minimum headroom date is accurate only to the month.


### 2.6 Volume line

Freight emits volume lines alongside its cash lines; the orchestrator passes them to bunker (D15). They never enter the liquidity path.


| Field | Type | Meaning |
| :--- | :--- | :--- |
| trade | enum | ASIA_EUROPE, ASIA_AMERICA, EUROPE_AMERICA, AFRICA_INTRA |
| month | date | First day of the month shipped |
| teu | Decimal | TEU shipped in the month (all volume classes) |
| pass_type | enum | base or scenario |
| label | enum | Provenance label |



### 2.7 Scenario parameter set

One model holds all drivers' parameters, so a joint event is a single object. ScenarioParams.zero_shock() MUST return the baseline parameters: every shock field reset, every state field unchanged (rule below).

Shock versus state (D28). Every field is classified as one or the other:

Shock fields, reset by zero_shock(): all % shocks, bp steps, credit_spread_change_bp, transit delays, volume shocks, consumption uplift and affected share, history replay, and refinancing_mode (reset to rollover).

State fields, identical in both passes: overrides, hedge tests, unapproved adjustments and zim_tranche_on.

The effect of a state change (with versus without a hedge, with versus without ZIM) is shown by comparing two runs, never as a scenario delta. The agent tools and the dashboard MUST support running and comparing such a pair (section 4).


| Block | Fields |
| :--- | :--- |
| Common | name, start_date, duration_days, default_shape (step, ramp, spike_revert), ramp_or_revert_weeks, source (preset, custom, auto_built, scheduled), event_ids, preset_id |
| Freight | rate_shock_pct by trade, volume_shock_pct by trade, transit_delay_days by trade, optional shape, optional history_replay (start and end dates), optional rate_shock_path per trade (explicit weekly date and %, overriding shock and shape; D37) |
| Bunker | vlsfo_shock_pct, hsfo_shock_pct, consumption_uplift_pct, uplift_affected_share, optional shape |
| Rates | rate_path per currency (USD, EUR): list of (date, step in bp), credit_spread_change_bp, refinancing_mode (rollover, unavailable), zim_tranche_on (bool) |
| FX | move_pct per currency vs USD, applied to USD per one foreign unit, optional shape per currency |
| Hedge tests | List of HedgeTest (below). Applied in both passes (D20) |
| Overrides | Dict of profile parameter key → value, this run only (slider moves). Each value checked against the profile's allowed range |
| Unapproved adjustments | List of pending adjustment IDs; allowed only when source = auto_built, and the run is labelled "includes unapproved adjustments" |


A driver's shape defaults to default_shape when absent (D5). Shapes act on the shock over time: step applies the full shock from start_date; ramp grows linearly to the full shock over ramp_or_revert_weeks; spike_revert applies the full shock at start_date and decays linearly to zero over ramp_or_revert_weeks. The shock returns to zero after duration_days unless duration_days is None (persistent).

Rate paths. Steps are cumulative: the rate change in force on a date is the sum of all steps dated on or before it. credit_spread_change_bp applies only to new borrowing: refinancing, and the ZIM tranche if drawn after T0. A hypothetical swap's fixed rate is set at T0 and never moves with it (D43). Existing contractual spreads don't change.

Freight shocks. rate_shock_pct moves each trade's route index. Realised rates follow through the pass-through logic in section 6 (spot, index-linked, contract). The shock is never applied directly to the realised rate.

A scenario MUST fail validation before any run if a field or override is outside its allowed range, or if a module-scoped preset sets fields of another driver.

HedgeTest: module (bunker, rates, fx), instrument (swap, fixed_price_supply, forward), target (for example fuel_all, floating_debt_usd, eur_operating_costs), coverage_pct, tenor_months, strike_rule (spot_at_T0 by default), start_date. Each module defines which targets and instruments it accepts.

Preset library (presets.json, D6): each preset has id, name, scope (joint or one module), description, a full ScenarioParams body and a rationale. Every preset has a permanent snake_case ID (D62), listed in the table below; presets.json, signals (suggested_preset), run summaries and scheduled runs use only these IDs. The sensitivity family (D50) is a set of single-driver presets, run one at a time and shown side by side. Custom scenarios carry preset_id = null. Numeric values are set in each module section.


| Preset ID | Name | Scope |
| :--- | :--- | :--- |
| red_sea_reescalation | Red Sea re-escalation | Joint |
| demand_slump | Demand slump | Joint |
| oil_spike | Oil spike | Joint |
| fed_cutting_cycle | Fed cutting cycle | Joint |
| eur_rally | EUR rally | Joint |
| em_stress | Emerging-market stress | Joint |
| freight_rate_reversal | Freight rate reversal | Joint |
| fed_cuts_ecb_holds | Fed cuts, ECB holds | Rates |
| both_hiking | Both hiking | Rates |
| spread_widening | Spread widening | Rates |
| credit_squeeze | Credit squeeze | Rates |
| broad_usd_strengthening | Broad USD strengthening | FX |
| cny_devaluation | CNY devaluation | FX |
| bunker_price_reversal | Bunker price reversal | Bunker |
| history_replay | History replay | Freight |
| rates_up_100bp, rates_down_100bp | Rates ±100 bp (USD and EUR together) | Sensitivity family (rates) |
| fx_eur_up_10, fx_eur_down_10 | EUR ±10% | Sensitivity family (FX) |
| fx_cad_up_10, fx_cad_down_10 | CAD ±10% | Sensitivity family (FX) |
| fx_gbp_up_10, fx_gbp_down_10 | GBP ±10% | Sensitivity family (FX) |
| fx_cny_up_10, fx_cny_down_10 | CNY ±10% | Sensitivity family (FX) |
| fx_inr_up_10, fx_inr_down_10 | INR ±10% | Sensitivity family (FX) |
| fx_brl_up_10, fx_brl_down_10 | BRL ±10% | Sensitivity family (FX) |



### 2.8 Signal schema (from the news engine)

This is the only contract with the news engine, agreed with the news engine team (D58). Each signal carries:


| Field | Type and allowed values | Meaning |
| :--- | :--- | :--- |
| event_id | String | Unique and stable; an updated event keeps its ID |
| version | Integer ≥ 1 | Starts at 1 and increases with each update to the same event |
| published_at | ISO 8601 datetime with timezone | Time of the source news; the event date for magnitude mapping |
| headline, summary | Short plain text | For the dashboard and agents |
| evidence_links | List of URLs, at least one | Sources |
| event_type | Closed enum, the single list for the whole system: central_bank_decision, bank_failure, credit_event, rating_change, financing_news, regulation, sanction, trade_disruption, port_disruption, commodity_move, fx_policy, capital_control, payment_rule, tax_rule, labour_action, market_structure, contract_season, competitor_action, company_announcement, supplier_news, fleet_change, other | Catalogue entries (3.3, sections 6–9) use only these values, and a test checks every catalogue event_types entry against this enum |
| kind | quantitative, qualitative, factual | Quantitative states a number; qualitative states a fact with a direction and no number; factual is context only |
| affected_drivers | Any of freight, bunker, rates, fx, liquidity, none |  |
| affected_trades | Any of ASIA_EUROPE, ASIA_AMERICA, EUROPE_AMERICA, AFRICA_INTRA | Same values as the volume line (2.6) |
| affected_currencies | ISO 4217 codes |  |
| affected_countries | ISO 3166-1 alpha-2 codes |  |
| named_entities | List of organisation names | Matched against the reference registers (3.4) |
| direction | up, down, mixed, none |  |
| magnitude | Number or null | Quantitative signals only |
| magnitude_unit | pct, bp, usd_per_t, usd_per_teu, other, or null |  |
| magnitude_subject | String or null | What the number refers to, for example SOFR, VLSFO, a freight route or a currency |
| suggested_preset | A preset ID from 2.7, or null |  |
| confidence | Number 0–1 | The news engine's confidence |
| expected_duration_days | Integer or null | Sets the duration of an auto-built scenario (below) |


submit_signal rejects any field outside its allowed values with a named error (10.6).

Example, a qualitative Red Sea re-escalation signal:

{
  "event_id": "evt_20261102_redsea_001",
  "version": 1,
  "published_at": "2026-11-02T06:30:00+00:00",
  "headline": "Attacks on shipping resume in the southern Red Sea",
  "summary": "Carriers report renewed attacks near Bab el-Mandeb; several services are re-routing via the Cape of Good Hope.",
  "evidence_links": ["https://example.com/news/red-sea-attacks"],
  "event_type": "trade_disruption",
  "kind": "qualitative",
  "affected_drivers": ["freight", "bunker"],
  "affected_trades": ["ASIA_EUROPE"],
  "affected_currencies": [],
  "affected_countries": ["YE", "EG"],
  "named_entities": [],
  "direction": "up",
  "magnitude": null,
  "magnitude_unit": null,
  "magnitude_subject": null,
  "suggested_preset": "red_sea_reescalation",
  "confidence": 0.8,
  "expected_duration_days": 90
}

Magnitude mapping (D60). A quantitative signal sets a scenario field directly only when its magnitude_subject and magnitude_unit match a row below; otherwise it is treated as qualitative. Mapped values must still pass the allowed ranges in 2.7.


| magnitude_subject | magnitude_unit | Sets |
| :--- | :--- | :--- |
| SOFR or ESTR | bp | A step in rate_path for that currency (USD for SOFR, EUR for ESTR), dated published_at |
| VLSFO or HSFO | pct | vlsfo_shock_pct or hsfo_shock_pct |
| A freight route (a WCI series ID in 3.6) or a trade (a 2.6 trade value) | pct | rate_shock_pct for the matching trade |
| A currency in the FX block (ISO 4217 code) | pct | move_pct for that currency, on USD per one foreign unit |


Confidence routing (D59, ASSUMPTION, thresholds in thresholds.yaml). Below 0.5: recommendation only. From 0.5 to below 0.7: may trigger a scenario, never an adjustment. 0.7 and above: may also trigger an adjustment proposal.

Duration (D61). expected_duration_days sets duration_days of an auto-built scenario; when null, the preset's duration is used.

Within the confidence limits above, the signal router assigns each signal one or more routes (D9): scenario (build or update an auto-built scenario), adjustment (propose a change to the believed present), recommendation (no engine input; go straight to the recommendation and brief agents).


### 2.9 Adjustment record

An adjustment is an approved, news-driven change to what the model believes is true today. It persists across runs until it expires or is revoked; a scenario is a what-if for one run.


| Field | Type | Meaning |
| :--- | :--- | :--- |
| adjustment_id | str | Unique ID |
| param_key | str | Catalogue key (3.3), or a liquidity availability key (3.7) |
| old_value, new_value | any | Value before and after, as resolved on effective_date |
| step | enum or None | small, medium, large (qualitative news); None for direct values (quantitative news) |
| effective_date | date | First date the new value applies; may be in the future |
| review_date | date | Date it expires or must be re-approved |
| event_ids | list[str] | Source events |
| justification | str | Agent's reasoning, citing the registers it checked |
| confidence | float 0–1 | Agent's confidence |
| verifier_result | enum | consistent, inconsistent, not_checked, with note |
| status | enum | proposed, approved, rejected, expired, revoked, withdrawn (a proposal from a superseded event version); approved adjustments may carry review_flag = true (10.6) |
| approver, decided_at | str, datetime | Human decision |
| proposed_by | str | Agent name and model version |


Only approved adjustments with effective_date ≤ date < review_date change values in a normal run. Every run record lists the adjustment IDs it applied.


## 3. Shared data

All company facts, beliefs and market data live in seven stores: the company profile, the adjustment layer, the adjustment catalogue, the reference registers, the exposure register, the market data store and the baseline path. Modules read from them and never keep their own copies.


### 3.1 Company profile

One YAML file, company_profile.yaml, immutable during normal use. A new version is created only by an administrator, for example after a quarterly report; slider moves never create a version. Each entry has this shape:

freight.collection_lag_days:
  value: 49
  label: DERIVED
  formula: "trade_receivables_30jun2026 / (q2_2026_group_revenue / 91)"
  allowed_range: [20, 90]
  unit: days
  adjustable: false            # not in the adjustment catalogue in v1
  rationale: "Quarter-end receivables over daily group revenue (Liner plus Terminal & Infrastructure); disclosed 30-day term shown alongside."

DISCLOSED entries MUST carry source: {report, page} and MUST have adjustable: false. DERIVED entries carry formula. ASSUMPTION entries carry allowed_range and rationale. The profile also holds profile_version and a content hash.

Disclosed anchors used across modules (module-specific anchors are listed in sections 6–9):


| Key | Value | Date | Source |
| :--- | :--- | :--- | :--- |
| Cash and cash equivalents | USD 3,150m | 30 Jun 2026 | IR p. 15 |
| Special fund securities and interests | USD 2,035m | 30 Jun 2026 | IR p. 15 |
| Unused credit lines | USD 725m | 30 Jun 2026 | IR p. 15 |
| Liquidity reserve (context only) | USD 5,909m | 30 Jun 2026 | IR p. 15 |
| Financial debt and lease liabilities | USD 7,153m, of which current USD 1,852m | 30 Jun 2026 | IR pp. 14–15 |
| Net debt (context only) | USD 1,969m | 30 Jun 2026 | IR p. 15 |
| Operating cash flow | USD 849m | H1 2026 | IR p. 14 |
| Trade receivables | USD 2,998m | 30 Jun 2026 | IR p. 13 |
| Transport volume; average freight rate | 6,684 TTEU; USD 1,406/TEU | H1 2026 | IR pp. 2, 10 |
| Liner revenue; revenue not assigned to trades | USD 10,461m; USD 1,065m | H1 2026 | IR pp. 2, 11–12 |
| Bunker consumption; average price paid | 2.4m t; USD 592/t | H1 2026 | IR pp. 10–11 |
| CO2 certificate cost (note only) | USD 112m | H1 2026 | IR pp. 10–11 |
| Variable-rate financial debt | EUR 1,999.1m | 31 Dec 2025 | AR p. 217 |
| Interest expense; money-market interest income; special fund income | USD 236m; USD 69m; USD 41m | H1 2026 | IR p. 9 |
| USD/EUR closing rates for converting EUR disclosures (D12) | Open item: verify which of 1.1756 and 1.1396 is the 30 Jun 2026 closing rate (both cited to IR p. 16); enter the 31 Dec 2025 rate from the AR | 31 Dec 2025 and 30 Jun 2026 | AR and IR, verify page |


Shared derived figures: baseline OCF run-rate = 849 ÷ 181 ≈ USD 4.69m per day; fuel per TEU = 2.4m t ÷ 6,684 TTEU ≈ 0.359 t/TEU; collection lag (DSO) = trade receivables ÷ (latest-quarter group revenue, Liner plus Terminal & Infrastructure, ÷ days in quarter), expected around 48–50 days.


### 3.2 Parameter resolution: three layers

Every parameter is read through one function, resolve(key, date, run_context), and never straight from the YAML. It applies three layers in order:


| Layer | Who changes it | How long it lasts | Example |
| :--- | :--- | :--- | :--- |
| Company profile | Administrator only | Until the next profile version | Disclosed consumption |
| Active adjustments | Agent proposes, human approves | From effective_date until review_date or revocation | Remove one bank's share of the credit lines |
| Run overrides | User sliders or a scenario's overrides | One run | MFR share 50% for this test |


Rules:

Parameters are time-dependent. An adjustment changes a value only from its effective_date, so a regulation taking effect next March changes the value from March, not today.

When two approved adjustments touch the same key, they apply in effective_date order; for the same date, the later approval wins.

A run override replaces the resolved value for every date in the horizon.

The result is checked against the profile's hard limits after each layer; a breach makes the run fail validation.

In an auto-built scenario, listed unapproved adjustments apply as if approved, and every output of that run carries the label "includes unapproved adjustments".

The run record stores the adjustment IDs applied and the override dictionary, so the run reproduces exactly.


### 3.3 Adjustment catalogue

The catalogue is an allowlist inside the profile of parameters agents may propose changing. Disclosed figures are never in it. Each entry has this shape:

bunker.scrubber_share:
  module: bunker
  event_types: [regulation, fleet_change, company_announcement]
  value_mode: step_or_direct     # step_only | step_or_direct
  steps: {small: -0.02, medium: -0.05, large: -0.10}   # signed amounts; sign flips for increases
  hard_limits: [0.0, 0.40]
  max_review_days: 180

Quantitative news may set a value directly (step_or_direct), within the hard limits. Qualitative news may only choose a step, and the catalogue supplies the amount, so a vague headline never becomes a falsely precise number.

Engine-side validation, validate_adjustment(proposal), MUST reject a proposal when: the key is not in the catalogue; the event type is not listed for that key; a direct value is proposed for a step_only key or from a qualitative signal; the new value breaks a hard limit; the review date is more than max_review_days after the effective date; or no event_ids are given.

Value modes are step_only, step_or_direct and switch (boolean keys, set directly from factual or quantitative signals; D48), each with its own validation test. Adjustments to sizing keys take effect from the first run whose T0 is on or after the effective date, because sizing values are resolved once at T0; validate_adjustment warns when a future-dated adjustment targets a sizing key (D52).

Catalogued keys by module (step amounts are set in each module section):


| Module | Parameters agents may propose changing |
| :--- | :--- |
| Freight | Pricing lag, γ (contract repricing share), contract leakage triggers and shares, non-USD invoicing share, delay-affected share |
| Bunker | Hub weights, scrubber share, MFR share and lag, supplier payment lag |
| Rates | New-issue spread premium, interest-bearing cash share, ZIM tranche switch |
| FX | Hedge ratios, timing of INR and BRL obligations, the INR and BRL obligations switch |
| Liquidity (shared) | Availability of each counterparty's share of a liquidity tier (3.7) |


FX hedge-ratio adjustments record a change in the company's actual hedging, for example a disclosed policy change. A recommended change to hedging is not an adjustment; it goes through the hypothetical hedge tools and the recommendation agent.


### 3.4 Reference registers

Reference registers are company facts the adjustment agent checks a news item against, to decide whether it is relevant to Hapag-Lloyd at all. They live in registers/ as YAML. Most entries are ASSUMPTION because details are not disclosed, and the dashboard MUST say so wherever a register drives a result.


| Register | Contents | Main label |
| :--- | :--- | :--- |
| Counterparties | 6–10 relationship banks: name, country, credit line share %, deposit share %, hedge counterparty flag. Shares sum to 100% per tier | ASSUMPTION |
| Operating footprint | Operating countries, currencies, cash location shares by currency, payment rails per country (for example SWIFT wires, SEPA, India RTGS/NEFT, Brazil TED/PIX, China CNAPS/CIPS), cash structure | Cash managed centrally: DISCLOSED (AR p. 218); the rest ASSUMPTION |
| Trades | The four trades, their WCI routes and main ports | DISCLOSED trade names (IR p. 10); ports ASSUMPTION |
| Fleet | Scrubber share of consumption, dual-fuel and LNG note | ASSUMPTION unless a figure is disclosed |
| Suppliers | Bunker hubs (Singapore, Rotterdam) and main bunker suppliers | ASSUMPTION |
| Regulatory touchpoints | Regimes that bind the company: EU ETS, FuelEU Maritime, IMO rules, EU/US/UN sanctions, capital controls in CNY, INR, BRL | DISCLOSED where cited (AR p. 24); the rest ASSUMPTION |


Registers answer "does this news touch us, and where?". They never create cash lines on their own (D27).


### 3.5 Exposure generator

generate(profile, as_of) -> ExposureRegister reads the resolved profile and builds a static register using each module's recipe (sections 6–9). Requirements:

Deterministic: no random numbers. The same profile, adjustments and as-of date always give the same register (D26).

Static only: contractual and planned quantities (volumes, contract cohorts, consumption, tranches, maturities, cash balances, FX exposures). It never applies shocks.

Each item has exactly one owner and a sub_id, formatted <module>:<kind>:<key>, for example freight:contract:AE-2026-03 or rates:tranche:USD-FL-02.

Each item carries synthetic = true, the label of the figure that sized it, and a short sizing note.

Links between modules (freight TEU feeding bunker MFR) are resolved in the orchestrator, never here.

No feedback loops in v1. A freight slump does not widen credit spreads by itself; correlated effects enter only through joint scenario parameters.

Debt maturities are built once, as one schedule; the generator assigns the USD share to rates and the EUR share to FX, so the two modules cannot drift apart.


### 3.6 Market data store

One SQLite table, market_data: series_id, date, value, unit, source, label, retrieved_at, filled (bool), days_since_print, confirmed (bool), source_ref (URL or API call).


| Series | Source | Frequency | Unit | Used by |
| :--- | :--- | :--- | :--- | :--- |
| FX_USD_PER_{EUR,CAD,GBP,CNY,INR,BRL} | ECB reference rates API (Frankfurter as fallback) | Daily | USD per unit | fx, all conversion |
| ESTR | ECB Data Portal | Daily | % | rates |
| SOFR | New York Fed Markets API | Daily | % | rates |
| UST_2Y | FRED DGS2 | Daily | % | rates alerts, swap tool |
| EA_2Y | ECB euro area yield curve, 2-year spot rate (series ID to confirm) | Daily | % | rates alerts, swap tool |
| BB_OAS | FRED ICE BofA BB US High Yield OAS (BAMLH0A1HYBB, to confirm) | Daily | % | rates spread |
| BRENT | FRED DCOILBRENTEU | Daily | USD/bbl, % change only | bunker proxy fill |
| WCI_SHA_RTM, WCI_SHA_LAX, WCI_RTM_NYC, WCI_COMPOSITE | Agent retrieval (Drewry weekly release or trade press) | Weekly | USD/FEU | freight |
| VLSFO_SIN, VLSFO_RTM, HSFO_SIN, HSFO_RTM | Agent retrieval (public bunker price reports) | Weekly | USD/t | bunker |


Rules:

One FX convention. Every FX rate is stored as USD per one unit of foreign currency, converted once at load. ECB rates are quoted per EUR, so USD per X = (USD per EUR) ÷ (X per EUR). Cross rates keep the label MARKET.

History. API series load 2 years of history automatically. WCI routes and the four fuel series load about 1 year of weekly history once, by hand, from a CSV.

Gaps. Daily series carry the last value forward on non-publication days, with filled = true and label MARKET. Weekly series are carried between prints by design. PROXY is used only for genuine stand-ins, such as Brent-filled daily fuel prices.

Agent retrieval (D8). A retrieved value that passes the shared checks is stored at once with confirmed = false. A value that fails a check goes to confirm_queue and is not used until a human confirms or corrects it on the dashboard. Any run that uses a value with confirmed = false carries the run-level flag "uses unconfirmed retrieved data", shown next to its results (D32).

Shared checks, run once per series at load: missing value; non-positive value; wrong unit (USD/FEU vs USD/TEU, USD/t vs USD/bbl); staleness, judged by days_since_print against the series' expected frequency (daily: stale after 5 days; weekly: stale after 10 days); a week-on-week move beyond the series' plausibility band (fuel ±40%, WCI ±80%, both ASSUMPTION) sends an agent-retrieved value to the confirm queue, and the dashboard raises the alert "possible major move, pending confirmation" (D31).

Licence line. Agent-retrieved series carry one line in the UI and exports: "publicly reported values, cited, used for non-commercial coursework." API series carry: "public reference data, cited."

Stale or missing material market data makes a run INCOMPLETE (section 4). Each module lists its material series.


### 3.7 Baseline path and liquidity tiers

The baseline path is deliberately simple: the company's normal cash flow, without any scenario. It adds the base-pass amounts of BASE_AMOUNT lines and the scenario deltas of all lines (2.2).

Starting liquidity. The last disclosed position (30 Jun 2026) rolled forward to T0 with baseline flows, labelled DERIVED. It MUST be replaced by the next disclosed position when a newer report is entered. The roll-forward updates T1 only; T2 stays at the disclosed USD 725m unless an adjustment changes it.

Baseline flow on each date t:

B(t) = \mathrm{OCF}_{daily} \cdot n(t) + \mathrm{Lease}(t) + \mathrm{Capex}(t) + \sum_{\text{BASE\_AMOUNT lines}} \mathrm{amount\_usd\_base}(t)


| Component | Value | Label |
| :--- | :--- | :--- |
| OCF run-rate | H1 2026 OCF ÷ 181 ≈ USD 4.69m per day, held flat; n(t) is the number of days the line covers (1 for days 1–91, days in month after) | DERIVED |
| Lease payments | Disclosed annual lease repayments ÷ 12, monthly, entered as a negative amount | DERIVED |
| Capex | Disclosed guidance, or the H1 run-rate, spread monthly, entered as a negative amount. Excludes FX_TRANSACTION amounts; if the source already includes them, FX_TRANSACTION is DELTA_ONLY (D34; open item: nature of the INR and BRL obligations) | DERIVED, or ASSUMPTION if no guidance |
| BASE_AMOUNT lines | Base-pass USD amounts from modules: dividend, debt principal and drawdown, FX transactions, hedge settlements, margin calls, and interest lines while interest_in_ocf = false | Per line |


Anchor adjustment (D69). The OCF run-rate embeds the market conditions of its period (H1 2026, the anchor period), so on its own the baseline would ignore market moves between that period and T0 on DELTA_ONLY lines. The baseline therefore carries one more labelled component, the market adjustment since anchor, A(t), written "anchor adjustment" below. It uses the same fixed-order swap as the weekly baseline change (4.10), from an anchor snapshot to the T0 snapshot:

Run the base pass (zero shock) with the anchor snapshot for every series group.

Swap the T0 snapshot in one driver group at a time, in the fixed engine order: freight series, bunker series, rate series, FX series. The last step equals the normal base pass at T0.

Driver part A_k(t) = the change in USD amounts of DELTA_ONLY lines on date t at step k. A(t) = the sum of the four parts = DELTA_ONLY lines at T0 prices minus the same lines at anchor prices. BASE_AMOUNT lines are excluded, because their base amounts already use T0 prices.

The baseline flow becomes B(t) + A(t); in the liquidity path below, B(t) includes A(t). The headline X = the change in 12-month minimum headroom from adding A, split by driver as the change at each step; the parts sum exactly to X.

A(t) is labelled ASSUMPTION under the weakest-label rule and is shown as its own component, never merged into the OCF run-rate. With T0 prices equal to the anchor, A(t) = 0 on every date. Scenario deltas are unchanged: both passes use the T0 snapshot. A is computed once per T0 and profile version and cached.

Anchor snapshot. Stored in company_profile.yaml under anchor_snapshot, with its period:


| Series group | Anchor value | Label |
| :--- | :--- | :--- |
| WCI routes | H1 2026 average of the loaded weekly values per route | DERIVED |
| VLSFO and HSFO at Singapore and Rotterdam | H1 2026 average per hub and grade from loaded history; the blended anchor is shown beside the disclosed average price paid, USD 592/t (IR pp. 10–11), and the gap is reported as a delivered premium | DERIVED; check value DISCLOSED |
| SOFR, ESTR, BB_OAS, UST_2Y, EA_2Y | H1 2026 average of daily values | DERIVED |
| FX, USD per EUR | H1 2026 average, 1.1667 | DISCLOSED (to verify, IR p. 16) |
| FX, other currencies | H1 2026 average of ECB daily values | DERIVED |
| Context only, not used in calculations | Rotterdam MFO 0.5% USD 540/t and SCFI USD 3,240/TEU at 30 Jun 2026 (period-end points, not averages) | DISCLOSED (IR p. 7) |


Loaded history MUST cover the anchor period. The anchor snapshot is replaced together with the OCF run-rate whenever a newer report updates it, and the anchor period is always the run-rate's period (D70).

Maturing financial debt excludes leases (D1): maturities within 12 months = current financial debt and leases (USD 1,852m) − current lease liabilities, labelled DERIVED, with the lease part taken from the disclosed annual lease repayments if not disclosed separately (ASSUMPTION).

Maturity dates (D29). The 12-month amount is spread evenly from 1 Jul 2026 to 30 Jun 2027; maturities before T0 are absorbed by the roll-forward. For horizon months after 30 Jun 2027, the same monthly amount continues (ASSUMPTION) until a newer report replaces it.

Liquidity path:

L(t) = L(t-1) + B(t) + \Delta(t)

Δ(t) is the net scenario delta in USD on date t from all modules, after FX conversion. With a zero-shock run, Δ(t) = 0 exactly.

Liquidity tiers, each split by counterparty using the counterparty register (D22):


| Tier | Starting amount | In headroom | Notes |
| :--- | :--- | :--- | :--- |
| T1 Operating cash | Cash and equivalents, rolled forward | Always | All cash flows land here. Split by deposit counterparty |
| T2 Committed credit lines | Unused lines, USD 725m | Always, as committed but conditional | Split by lending bank. Drawn notionally only when T1 falls below the reserve |
| T3 Liquid investments | Special fund, USD 2,035m | Only when count_liquid_investments = true (default false) (D21) | Shown on the dashboard either way |
| T4 Margin-eligible | 25% of T1 (ASSUMPTION) | Used only when a hypothetical swap is tested | Feeds the separate margin test |


Headroom: H(t) = T1(t) + T2(t) [+ T3(t) if switched on] − minimum reserve. The minimum operational reserve is USD 750m, labelled ASSUMPTION (roughly 15 days of transport expenses at the H1 2026 rate).

Shortfall tier. When T1(t) is below the reserve but T1 + T2 is not, the dashboard reports "covered only by drawing on the credit lines". When T1 + T2 is below the reserve, the reserve is breached.

Availability adjustments. An approved adjustment with key liquidity.<tier>.<counterparty> sets that counterparty's available fraction of a tier (0 to 1) from its effective date. It reduces the tier without creating a cash line, because some news changes how much existing cash or credit can be used rather than adding cash flows. Removing a counterparty's deposits reduces T1 once, by that counterparty's share of the T1 balance on the effective date; later flows land with the remaining counterparties (D30). Steps: small 25%, medium 50%, large 100% reduction (ASSUMPTION); a confirmed bank failure is always large. Catalogue event types for these keys: bank_failure, credit_event, sanction, capital_control.

Documented limitations: a flat OCF run-rate leaves all volatility to the scenario deltas; group-level liquidity ignores trapped or entity-level cash; after day 91 the minimum is accurate only to the month.


## 4. Shared engine

run_scenario is one deterministic function: two in-memory passes, one FX conversion, paired deltas, one liquidity path, then status, attribution and a single run record. It contains no LLM call, no network call and no wall-clock reading other than the as-of date. Nothing is written to the database until the run is complete.


### 4.1 Orchestrator

Signature: run_scenario(params: ScenarioParams, as_of: date | None = None) -> RunResult. When as_of is None, T0 is the latest date for which all daily API series are loaded.

Validate. Check every field and override against its allowed range, module-scoped presets against their scope, and unapproved adjustments against source = auto_built. A failure raises ScenarioValidationError with every problem listed; nothing runs.

Build the run context. Resolve parameters for every date in the horizon (3.2); take a market data snapshot as of T0 (4.2); generate the exposure register (3.5); record the profile version, adjustment IDs and series as-of dates.

Baseline pass. With params.zero_shock(): freight, then bunker (reading freight's base volume lines), then rates, then FX's own exposures.

Scenario pass. The same pipeline with params; bunker reads freight's scenario volume lines.

Snap dates of both passes to the timeline (2.5).

FX conversion layer (section 9A): converts base lines at the base FX path and scenario lines at the scenario FX path, fills the conversion fields, and adds the FX policy hedge settlement lines (owner fx). Conversion is linear, so converting line by line or converting the net gives the same USD total. Netting is used only to size policy hedges and to report net positions per currency, never as a step that changes totals. CNY and GBP translation exposure is returned as memo items.

Pair and subtract. Full outer join on the stable key; missing side = 0. For each pair: delta_native = amount_scenario − amount_base; delta_usd = amount_usd_scenario − amount_usd_base.

Liquidity path (3.7 and 4.4) for the baseline and the scenario.

Status and alerts (4.5, 4.6).

Attribution (4.3).

Run record (4.7), written in one SQLite transaction with all cash lines.

A zero-shock run MUST give delta_usd == 0 on every pair and identical baseline and scenario paths.

Sizing versus time-varying parameters. The generator sizes exposure items using values resolved at T0. Parameters that can change over time (for example scrubber share, MFR share, hedge ratios) are read by modules through resolve(key, date) during the run, so an adjustment with a future effective date takes effect on that date. Each module section marks its parameters as sizing or time-varying.

Pairs of runs (D28). State effects (a hedge test, the ZIM switch, an override) are never scenario deltas. run_pair(params, state_change: dict) -> PairResult runs the same scenario twice, once as given and once with the state change applied, and returns both run IDs plus the difference in liquidity path, minimum headroom, attribution bars and line totals, labelled "state comparison".


### 4.2 Market data snapshot and price paths

The snapshot holds, for each series, the latest value dated on or before T0, its days_since_print, filled, confirmed and label. Modules never query the store directly; they read through the run context.

Shared path builders, used by every module:

base_path(series, dates): the T0 value held flat for every date (no forward curves; forward points are zero).

scenario_path(series, dates, shock_pct, shape): base × (1 + shock_pct × f(t)), where f(t) is the shape factor (0 to 1) from 2.7, the same f used in sections 6–9.

rate_path(currency, dates, steps): T0 benchmark + the cumulative sum of steps dated on or before each date.

fx_paths(currency, dates): base and scenario USD per unit, built with scenario_path from the FX block.

A module's material series are listed in its section. If any is stale or missing at T0, the run completes but its status is INCOMPLETE (4.6).


### 4.3 Attribution

Attribution splits the total net delta into one bar per driver plus one FX bar, exactly. For each paired line i with native base amount a_i, scenario amount b_i, base FX rate x_i and scenario FX rate y_i on its date:

\text{Driver bar}_d = \sum_{i \in d} (b_i - a_i)\, x_i \quad d \in \{\text{freight}, \text{bunker}, \text{rates}\}

\text{FX bar} = \sum_{\text{all } i} b_i\,(y_i - x_i) \; + \sum_{i \in \text{fx}} (b_i - a_i)\, x_i

\text{Total net delta} = \sum_i (b_i y_i - a_i x_i) = \text{freight} + \text{bunker} + \text{rates} + \text{FX}

The second term of the FX bar covers FX-owned lines whose native amount changes, mainly the FX policy hedge settlements. Hypothetical bunker and rates hedges are state, so their deltas come only from price moves and stay in their own driver's bar.

Attribution is computed twice: over the full horizon, and cumulatively up to the scenario's minimum-headroom date. The bars MUST sum to the total within USD 0.01.

Gross adverse delta per driver. For the desk exception, each module declares gross line codes and offset line codes (section 5). G_d is the largest cumulative adverse USD delta of driver d's gross lines at any date in the horizon (zero if never adverse). For rates, G_d is the larger of the debt side and the cash side, each computed alone. For FX, G_d uses the conversion effect before policy hedge settlements. G_d for freight, bunker and rates is measured at base FX, the same basis as the driver bars, so a currency move never triggers another driver's desk exception. Desk exceptions are skipped when H_b* ≤ 0, since the status is already CRITICAL.


### 4.4 Liquidity tests

For each date on the timeline the engine computes the baseline path L_b(t) and the scenario path L_s(t) = L_b(t) + cumulative Δ(t), the tier balances and the headroom H(t) (3.7). It then reports:


| Output | Definition |
| :--- | :--- |
| Baseline minimum headroom H_b* and date | Lowest H_b(t) in the horizon |
| Scenario minimum headroom H_s* and date | Lowest H_s(t) in the horizon |
| Headroom consumed c | (H_b* − H_s*) ÷ H_b*, when H_b* > 0. Negative c means the scenario improves headroom (an opportunity) |
| First breach date | First t where the counted tiers fall below the USD 750m reserve, in either path |
| Shortfall tier at the minimum | "Within operating cash" (T1 ≥ reserve); "covered only by drawing on the credit lines" (T1 < reserve ≤ T1 + T2); "needs liquid investments" (only if T3 is counted); "reserve breached" |
| Margin-eligible test | Run only when a hedge test includes a swap: T4(t) minus cumulative MARGIN_CALL outflow; any negative value is a margin-eligible shortfall |


A path breaches the reserve exactly when H(t) < 0 for some t, that is, when the counted tiers fall below USD 750m. Section 4.6 uses "breach" only in this sense.

When H_b* ≤ 0 the baseline itself breaches the reserve: c is not computed, the status is CRITICAL regardless of the scenario, and the dashboard shows the baseline breach amount and date.

Resolution is daily for days 1–91 and monthly after; a monthly minimum is reported as the month-end date with the note "accurate to the month".


### 4.5 Tier A: market alerts (primary)

One rule engine evaluates every module's rules on observed market data after each data load. Rules live in thresholds.yaml:


| Field | Meaning |
| :--- | :--- |
| rule_id, module | Unique ID; owning module |
| series | One series, or two for spreads and divergences |
| type | pct_move, bp_move, vol_multiple, spread_change, divergence, vol_regime |
| window | 1 day, 1 week, or N observations |
| thresholds | warning and severe values |
| lookback | For volatility: 60 daily or 26 weekly observations |
| on_severe | none or request_auto_build |


Volatility is the standard deviation of log changes over the lookback; vol_multiple = |latest change| ÷ that volatility. vol_regime compares short-window volatility with its long-window average (daily series: 20 days against 1 year; weekly series: 8 weeks against 52 weeks): calm below 1.25×, elevated 1.25–2.0×, stressed above 2.0× (ASSUMPTION).

A threshold written "X or Y" fires when either condition is met. The latest change in vol_multiple is a log change, the same unit as the volatility. Alerts on unconfirmed retrieved values still fire and carry the unconfirmed flag.

Shared defaults (module sections may add rules), all PROPOSED ILLUSTRATIVE SETTINGS:


| Series group | Warning | Severe |
| :--- | :--- | :--- |
| FX, major (EUR, CAD, GBP), daily | 1.0% or 2.5× vol | 2.0% or 4× vol |
| FX, emerging (INR, BRL, CNY), daily | 0.75% or 2.5× vol | 1.5% or 4× vol |
| WCI routes, weekly | 10% or 2.5× vol | 20% or 4× vol |
| VLSFO and HSFO, weekly | 7.5% or 2.5× vol | 15% or 4× vol |
| Brent, daily | 4% | 7% |
| 2-year yields, weekly | 15 bp | 30 bp |
| BB credit spread, weekly | 25 bp | 50 bp |


Two data alerts sit beside these: "possible major move, pending confirmation" (D31) and "stale material series".

A severe alert with on_severe = request_auto_build emits an AutoBuildRequest (rule, series, move, event IDs). The scenario builder agent turns it into an auto-built scenario (section 10). At most one request per rule per day is emitted. Central bank and policy events reach the system through the news engine, not through these rules.


### 4.6 Tier B: scenario status

Tier B judges a run, not the market. Thresholds sit in thresholds.yaml, labelled PROPOSED ILLUSTRATIVE SETTINGS.


| Status | Condition |
| :--- | :--- |
| LOW | c < 10%, including any favourable run. When c ≤ −10%, the informational label OPPORTUNITY is shown beside LOW |
| WATCH | 10% ≤ c < 25% |
| HIGH | 25% ≤ c < 50% |
| CRITICAL | c ≥ 50%; or the scenario breaches the reserve; or the baseline breaches it (H_b* ≤ 0) |
| INCOMPLETE | A material series is stale or missing, or a required profile parameter is missing, and the computed status is LOW or WATCH (precedence below). The computed figures are still shown, marked incomplete |


Precedence. When material data is stale or missing, a computed CRITICAL or HIGH keeps that status and carries the flag "incomplete data"; a computed WATCH or LOW becomes INCOMPLETE. INCOMPLETE never replaces a more severe status, because missing data never means low risk.

Run-level flags, shown beside every result:

Proxy-based (D4): material lines use PROXY prices. A status that would be LOW is raised to WATCH, with this reason.

Uses unconfirmed retrieved data (D32).

Includes unapproved adjustments (auto-built runs only).

Synthetic register: always on, because exposures are generated.

Desk exceptions fire regardless of the net status: a driver's gross adverse delta G_d ≥ 25% of H_b* (D13, D55); a margin-eligible shortfall; a stale or missing material series.


### 4.7 Alert explanation, run record and audit log

Every alert (Tier A, Tier B or desk exception) carries one explanation object: alert_id, tier, rule_id or status, the inputs with labels and as-of dates, the formula, the threshold, the observed value, a reason code, limitations, linked playbook entries, event_ids and run_id.

Each alert has a state: open, acknowledged (by a named user) or cleared (condition no longer met), with the times it opened, was acknowledged and cleared. The dashboard and the weekly brief use the state to separate new alerts from ones already seen.

The run record is one JSON document per run in run_records, with its cash lines in cash_lines:


| Field | Contents |
| :--- | :--- |
| run_id, created_at, as_of | Identity and time |
| params | Full scenario parameters, source, preset ID and event IDs |
| profile_version, profile_hash | Which profile |
| adjustment_ids | Approved adjustments applied, plus any unapproved ones (auto-built) |
| market_snapshot | Per series at T0: the snapshot value itself, its value date, days_since_print, confirmed |
| lines | Reference to all cash lines of both passes with conversion fields; volume lines; FX memo items |
| results | Liquidity paths, tiers, headroom, c, breach dates, shortfall tier, margin test, attribution (both versions), G_d per driver, module metrics |
| status, flags, alerts | Tier B status, flags and explanation objects |
| pair_id | Set when the run is half of a state comparison |
| review | Reviewer, decision (accepted, overridden, escalated), rationale, time |
| superseded_by | Set when an auto-built run is replaced by a re-run for a newer event version (10.6); the old run is marked superseded |


replay(run_id) MUST rebuild a run from its record alone (profile version, adjustments, overrides, stored market snapshot values, never the live store) and reproduce every result, even after retrieved data has been corrected.

The audit log is append-only and records data loads and confirmations, adjustment proposals and decisions, runs, reviewer decisions and recommendation decisions, each with actor, time and object ID.


### 4.8 Agent tool interface

Agents use only these Python functions; each returns JSON-serialisable results. Agents decide what to run and what it means; the engine alone does the arithmetic.


| Tool | Purpose |
| :--- | :--- |
| run_scenario(params) | Run one scenario; returns run ID and a summary |
| run_pair(params, state_change) | Compare two states under the same scenario (4.1) |
| test_hedge(hedge_test, params) | Hypothetical hedge tool: run_pair with and without the hedge, plus the module's hedge metrics |
| get_run(run_id, sections) | Read parts of a run record |
| get_metric(run_id, name) | One metric with its label and formula |
| get_market_series(series_id, start, end) | Market history with labels and flags |
| search_events(query, since, filters) | News engine signals in the 2.8 format (10.6) |
| get_register(name, query) | Look up a reference register (3.4) |
| propose_scenario(params) | Validate, and run if valid; invalid proposals return the errors |
| propose_adjustment(proposal) | Validate against the catalogue (3.3) and queue as proposed |
| list_adjustments(status) | Read the adjustment layer |
| get_alerts(since, tier, state) | Read alerts and their explanation objects, filtered by tier and state (open, acknowledged, cleared) |
| get_playbook(module, trigger) | Read a module's playbook entries for an alert rule, status or preset type |
| web_search(query), fetch(url) | Read-only web access; data retrieval agent only |
| submit_market_value(series_id, value, date, unit, source_url) | Runs the shared checks (3.6), then stores the value as unconfirmed or sends it to the confirm queue; data retrieval agent only. The agent never writes to the store directly |


Approving, rejecting or revoking an adjustment, confirming retrieved data and recording reviewer decisions are dashboard functions for humans only. They MUST NOT be exposed as agent tools.

Each agent session may trigger at most 20 runs (run_scenario, run_pair, test_hedge and valid propose_scenario calls), ASSUMPTION. Further calls return an error naming the limit, so a looping agent cannot flood the run records.

Output formats (D65). All output amounts are USD millions; every figure carries a provenance label (2.4); alert tiers are written market (Tier A), scenario (Tier B) and desk_exception.

run_scenario returns a run summary:


| Field | Contents |
| :--- | :--- |
| run_id, as_of | Identity and T0 |
| scenario | name, source (preset, custom, auto_built, scheduled), preset_id (2.7, or null), event_ids |
| status | LOW, WATCH, HIGH, CRITICAL or INCOMPLETE (4.6) |
| opportunity | true when c ≤ −10% (4.6) |
| flags | synthetic_register, proxy_based, uses_unconfirmed_data, includes_unapproved_adjustments, incomplete_data (booleans) |
| headroom | baseline_min_usd_m, baseline_min_date, scenario_min_usd_m, scenario_min_date, consumed_pct (c in %, null when H_b* ≤ 0), first_breach_date (or null), shortfall_tier: within_operating_cash, credit_lines_needed, liquid_investments_needed or reserve_breached (the four shortfall tiers of 4.4) |
| attribution_usd_m | freight, bunker, rates, fx, total (full horizon, 4.3) |
| desk_exceptions | List of driver, gross_adverse_usd_m (G_d), pct_of_headroom, date |
| alert_ids | Alerts raised by the run (4.7) |


Example, the joint Red Sea test of 14.3:

{
  "run_id": "run_20261031_joint_red_sea",
  "as_of": "2026-10-31",
  "scenario": {"name": "Red Sea re-escalation", "source": "preset", "preset_id": "red_sea_reescalation", "event_ids": []},
  "status": "LOW",
  "opportunity": false,
  "flags": {"synthetic_register": true, "proxy_based": false, "uses_unconfirmed_data": false, "includes_unapproved_adjustments": false, "incomplete_data": false},
  "headroom": {"baseline_min_usd_m": 500.0, "baseline_min_date": "2027-09-30", "scenario_min_usd_m": 462.441, "scenario_min_date": "2027-09-30", "consumed_pct": 7.51, "first_breach_date": null, "shortfall_tier": "within_operating_cash"},
  "attribution_usd_m": {"freight": 15.96, "bunker": -53.519, "rates": 0.0, "fx": 0.0, "total": -37.559},
  "desk_exceptions": [{"driver": "freight", "gross_adverse_usd_m": 168.0, "pct_of_headroom": 33.6, "date": "2026-12-31"}],
  "alert_ids": ["alert_desk_freight_001"]
}

run_pair and test_hedge return {pair_id, run_a, run_b, difference, label}, where run_a and run_b are run summaries, label is "state comparison", and difference (run_b minus run_a) holds min_headroom_usd_m, attribution_usd_m (the five bars) and line_totals_usd_m (by line code). test_hedge runs the hedge in run_b and adds hedge_metrics: saving_adverse_usd_m (the hedge's improvement in minimum headroom under the given scenario), cost_favourable_usd_m (its reduction in minimum headroom under the mirrored scenario, every shock sign reversed), settlement_cash and margin_cash (lists of date and USD m), and margin_test (passed, and the lowest margin-eligible balance).

get_metric returns {name, value, unit, label, formula, run_id}.


### 4.9 Engine walkthrough

A toy run with three lines shows the two passes, pairing, attribution, the path and the status end to end. The acceptance test test_engine_walkthrough MUST reproduce every figure below (USD m unless stated).

Fixture. T0 = 31 Oct 2026. Starting T1 = 1,000, T2 = 200, T3 not counted, reserve = 750. The OCF run-rate and lease payments are set to zero so the arithmetic stays visible; the only baseline item is a capex payment of −150 on 15 Dec 2026. Base FX is 1.15 USD per EUR. All dates fall within days 1–91, so nothing is snapped.


| Stable key (owner, code, date, currency, sub_id) | Treatment | Base native amount |
| :--- | :--- | :--- |
| freight, FREIGHT_REVENUE, 30 Nov 2026, USD, freight:spot:AE | DELTA_ONLY | +100.00 |
| bunker, FUEL_PURCHASE, 30 Nov 2026, USD, bunker:month:2026-11 | DELTA_ONLY | −40.00 |
| fx, FX_OPERATING_COST, 30 Nov 2026, EUR, fx:eur_opex:2026-11 | DELTA_ONLY | −20.00 EUR |


Baseline path. DELTA_ONLY lines do not enter it. H_b = 1,000 + 200 − 750 = 450 until 14 Dec, then 300 from 15 Dec. So H_b* = 300 on 15 Dec 2026.

Run 1: zero shock. Both passes give the same lines: 100.00, −40.00 and −20.00 EUR × 1.15 = −23.00. Every delta is exactly 0, H_s* = H_b* = 300, c = 0% and the status is LOW.

Run 2: bunker and FX shock. VLSFO and HSFO +20% and EUR +10% against USD (1.15 → 1.265 USD per EUR), both as a step from T0. In this toy, the fuel purchase scales one-for-one with price.


| Line | a (base native) | b (scenario native) | x (base FX) | y (scenario FX) | a·x | b·y | Delta USD |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Freight revenue | 100.00 | 100.00 | 1 | 1 | 100.00 | 100.00 | 0.00 |
| Fuel purchase | −40.00 | −48.00 | 1 | 1 | −40.00 | −48.00 | −8.00 |
| EUR operating cost | −20.00 | −20.00 | 1.15 | 1.265 | −23.00 | −25.30 | −2.30 |
| Total |  |  |  |  |  |  | −10.30 |



| Attribution bar | Calculation | Result |
| :--- | :--- | :--- |
| Freight | (100.00 − 100.00) × 1 | 0.00 |
| Bunker | (−48.00 − (−40.00)) × 1 | −8.00 |
| Rates | No lines | 0.00 |
| FX | −20.00 × (1.265 − 1.15) + (−20.00 − (−20.00)) × 1.15 | −2.30 |
| Sum | Equals the total delta exactly | −10.30 |


Path and status. Δ(30 Nov) = −10.30, so H_s = 450 until 29 Nov, 439.70 from 30 Nov and 289.70 from 15 Dec. H_s* = 289.70 on 15 Dec 2026. Headroom consumed c = (300 − 289.70) ÷ 300 = 3.43%, so the status is LOW and nothing breaches. Bunker's gross adverse delta G = 8.00 is 2.67% of H_b*, below 25%, so no desk exception fires.


### 4.10 Scheduled runs and baseline change

Scheduled runs keep results current in a quiet week, and the baseline change explains what moved liquidity since the previous run.

Scheduled runs (D66). A deterministic job, with no LLM, runs after each daily market data load. It runs the baseline and a standard set (red_sea_reescalation, demand_slump, oil_spike, fed_cutting_cycle, eur_rally, em_stress and the sensitivity family) and stores them with source = scheduled. Scheduled runs count against neither the agent run cap nor the auto-build cap. The weekly brief and the dashboard read the latest scheduled runs.

Baseline change (D71). Each scheduled run explains the change in 12-month minimum headroom since the previous scheduled baseline, split by driver. Because the baseline carries the anchor adjustment (3.7, D69), it already reflects market prices, so the weekly change is simply the difference between two anchored baselines.

Run the new T0 with the previous market snapshot, anchor adjustment included. Other changes = its minimum headroom minus the previous baseline's (time roll, new or expired adjustments, profile updates).

Swap in today's snapshot one driver group at a time, in the fixed engine order: freight series (WCI), bunker series (VLSFO, HSFO, Brent), rate series (SOFR, ESTR, BB_OAS, UST_2Y, EA_2Y), FX series, recomputing the anchored baseline at each step. Each step's change in minimum headroom is that driver's effect; the last step is today's baseline.

The parts sum exactly to the total change, because each step starts from the previous one. Documented limitation: the split depends on the fixed order.

The result is stored as a baseline_change record: date, previous and current run IDs, total, by driver (freight, bunker, rates, fx), other. It is shown on the overview, and the weekly brief uses the same method against the scheduled baseline of seven days earlier for "What changed" (10.5), for example "Compared with prices held at last week's levels, fuel price moves reduced 12-month minimum headroom by USD X m."


## 5. Module template

Every driver module implements one Python interface and is specified under the same sixteen headings. A new driver (section 15) follows the same template and needs no engine change beyond registering its line codes.


### 5.1 Interface

class DriverModule(Protocol):
    name: str                              # "freight" | "bunker" | "rates" | "fx"
    line_codes: set[LineCode]              # codes this module may emit (2.2)
    gross_codes: set[LineCode]             # used for G_d (4.3)
    offset_codes: set[LineCode]            # recoveries and hedges, excluded from G_d
    material_series: list[str]             # stale or missing -> INCOMPLETE
    required_params: list[str]             # profile keys the module reads

    def build_exposures(self, ctx: ProfileContext) -> list[ExposureItem]: ...
    def run(self, ctx: RunContext, params: ScenarioParams,
            exposures: list[ExposureItem], inputs: ModuleInputs) -> ModuleOutput: ...
    def alert_rules(self) -> list[AlertRule]: ...
    def playbook(self) -> list[PlaybookEntry]: ...
    def hedge_targets(self) -> list[HedgeTarget]: ...   # empty if no hedge tool


| Type | Contents |
| :--- | :--- |
| ProfileContext | as_of, horizon dates, resolve(key, date) |
| RunContext | ProfileContext plus pass_type, the market snapshot, the path builders (4.2), the shape function and the calendar helpers |
| ModuleInputs | Outputs of upstream modules for the same pass; only bunker uses it (freight volume lines) |
| ModuleOutput | cash_lines, volume_lines (freight only), metrics (name → value, unit, label, formula), notes (items not modelled), diagnostics |
| ExposureItem | sub_id, owner_spec, kind, currency, quantities and dates, label, synthetic, sizing note |
| PlaybookEntry | trigger (alert rule, status or preset type), actions (ordered short texts), hedge_tool (optional target), review_owner |
| AlertRule | As in 4.5 |



### 5.2 Rules every module follows

Pure function. run reads only from its arguments; no file, database, network or LLM access.

Full gross native amounts in both passes. Never net, convert, compute liquidity, status or attribution.

One line code per line, from the module's own list, with sub_id from the exposure item it came from.

Shocks only through shared helpers. Prices and rates come from base_path, scenario_path and rate_path; time shapes from the shared shape function.

Both directions. Every rule works for up and down moves; an upside is reported, never suppressed.

Label every line with one provenance label (2.4); synthetic items carry synthetic = true.

Dates on the calendar. Emit exact calendar dates inside the horizon; the orchestrator snaps them. Items beyond the horizon go to a watch list in metrics, never to cash lines.

State versus shock. Hedge tests and state switches are applied identically in both passes (D28).


### 5.3 Hypothetical hedge tool pattern

No fuel or freight hedges are disclosed, and the only disclosed interest rate swap (USD 2.2m) is immaterial, so they start fully unhedged: bunker and rates offer a hypothetical hedge tool instead of a hedge register, and freight has none in v1 (6.7). FX generates its hedges from the disclosed policies; changing those ratios is its hedge tool.

A HedgeTest (2.7) names a target the module lists in hedge_targets(), the instrument, coverage, tenor and strike rule.

The module emits HEDGE_SETTLEMENT lines (and MARGIN_CALL lines for swaps) in both passes.

test_hedge runs the scenario with and without the hedge (run_pair) and reports: the saving in the adverse direction, the cost in the favourable direction, settlement and margin cash by date, and the margin-eligible test.

Coverage percentages appear only inside the tool. Outside it, the module shows the stated fact "Unhedged: 100% (no hedges disclosed)", or for FX the policy hedge ratio per currency.


### 5.4 Standard section structure for sections 6–9


| No. | Heading | What it contains |
| :--- | :--- | :--- |
| 1 | Purpose and boundary | What the module owns, what it leaves to others, and why |
| 2 | Evidence anchors | Disclosed and derived figures with report and page |
| 3 | Exposure recipe | How the generator builds the module's register, deterministically |
| 4 | Market inputs | Series used; which are material |
| 5 | Calculation logic | Formulas, timing rules, lags and their labels |
| 6 | Scenario parameters and presets | The module's block of ScenarioParams and numeric preset values |
| 7 | Hypothetical hedge tool | Targets, instruments, settlement and margin formulas (or FX policy hedges) |
| 8 | Outputs | Line codes, gross and offset codes, volume lines, dashboard metrics |
| 9 | Tier A alert rules | Module rules in addition to the shared defaults (4.5) |
| 10 | Agent-adjustable parameters | Catalogue entries: event types, steps, hard limits |
| 11 | Notes for items not modelled | Dashboard notes, with disclosed size where available |
| 12 | Playbook | Actions by trigger, linked to the hedge tool where relevant |
| 13 | Back-tests and sanity checks | Reproductions of disclosed figures, with tolerances |
| 14 | Worked example | One named scenario, hand-computed |
| 15 | Module acceptance tests | Tests that run inside the shared suite (section 14) |
| 16 | Open items | Unresolved inputs and their defaults |


Section 9B follows the same headings, except that its calculation logic sits in the conversion layer (9A) and its hedge tool is the policy hedge ratios (9B.4).


### 5.5 Worked examples

Each worked example runs one named scenario on a small fixture profile: a reduced copy of the real profile with round numbers (for example one trade, two contract cohorts, a 3-month window) so every figure can be checked by hand. The fixture is stored in tests/fixtures/<module>_example.yaml, the generator builds the register from it with no randomness (D26), and the expected figures are stated to the cent. Fixtures set the anchor snapshot equal to the T0 snapshot, so the anchor adjustment is zero and the worked examples are unaffected (D69). The module's acceptance test runs the same fixture through the real engine and MUST reproduce every stated figure.

Section 14 runs each module's own worked-example scenario on the combined fixture and reproduces its figures. The joint Red Sea run is a separate, hand-computed test of the combination: the transfer of TEU volumes from freight to bunker, FX conversion of other modules' lines, attribution that adds up exactly, and a desk exception firing while the net status stays LOW.


## 6. Freight module

Freight turns route-index moves into dated freight revenue cash, by trade, volume class and currency. Its value is timing: when a market move reaches cash through pricing lags, contract resets and collection lags, not only how large it is.


### 6.1 Purpose and boundary

Owned: Liner Shipping freight revenue on the four 2026 trades, from shipments after T0, collected after the collection lag. Volume (TEU) per trade and month, passed to bunker.

Not owned: fuel moves and MFR (bunker; a freight shock is a market rate move with fuel unchanged); FX conversion (FX layer); revenue not assigned to trades, mainly demurrage and detention (note only); Terminal & Infrastructure; cost lines; collections of receivables outstanding at T0 (inside the OCF run-rate and unaffected by scenarios).

Base rate. The disclosed all-in average rate per trade, with no surcharge stripping (P0, the H1 2026 rate at the H1 2026 index level). Applying the shock to the all-in base overstates the freight impact by roughly the fuel share of the rate: about USD 213/TEU (0.359 t × USD 592/t) of USD 1,406/TEU, about 15% (DERIVED). The bias is conservative and shown on the dashboard. A base rate with surcharges stripped is v2 scope. P0 MUST be replaced by the newest quarterly trade rate whenever a report is entered (the Q3 2026 report is due shortly), together with the anchor snapshot (D70). P0 reflects the anchor-period index level (D35); index moves since then reach the baseline through the anchor adjustment (3.7, D69), and the dashboard shows the flag "base rate from <period>; index has moved X% since then", where X is the volume-weighted change of the four route indices from that period's average to T0.


### 6.2 Evidence anchors


| Item | Value | Label | Source |
| :--- | :--- | :--- | :--- |
| Transport volume; average freight rate, H1 2026 | 6,684 TTEU; USD 1,406/TEU | DISCLOSED | IR pp. 2, 10 |
| Volume by trade, H1 2026 (TTEU) | 1,866 / 2,472 / 1,341 / 1,004 (sum 6,683) | DERIVED from the original spec's bridge; trade labels to verify | IR pp. 10–11 |
| Rate by trade, H1 2026 (USD/TEU) | 1,367 / 1,407 / 1,467 / 1,391 | DERIVED as above | IR pp. 10–11 |
| Volume and rate by trade, H1 2025 adjusted | 1,872 / 2,456 / 1,398 / 862 TTEU; 1,310 / 1,493 / 1,506 / 1,245 USD/TEU | DERIVED as above | IR pp. 10–11 |
| Liner revenue; revenue not assigned to trades | USD 10,461m; USD 1,065m (H1 2025: 1,089m) | DISCLOSED | IR pp. 2, 11–12 |
| Trade receivables, 30 Jun 2026 | USD 2,998m | DISCLOSED | IR p. 13 |
| Predominant payment term | Within 30 days of invoice | DISCLOSED | AR p. 185 |
| Direct-customer relationships | Generally up to 36 months | DISCLOSED | AR p. 27 |
| Freight-rate risk class | Critical impact, Medium probability | DISCLOSED | AR pp. 43–44 |
| SCFI (context only, not an input) | USD 1,656/TEU (31 Dec 2025); USD 3,240/TEU (30 Jun 2026) | DISCLOSED | AR p. 30; IR p. 7 |
| Freight hedges | None disclosed, so coverage equals the contract share | DISCLOSED (absence) | AR pp. 215–220 |


Trade order in the derived rows is the IR table order, mapped as Asia–Europe, Asia–America, Europe–America, Africa & Intraregional. The coding agent MUST NOT treat this mapping as verified until checked against IR p. 10 (open item).


### 6.3 Exposure recipe

All items are sizing values resolved at T0 unless marked time-varying.

Monthly volume per trade. V(k, q) = group volume in the same quarter of the prior year × trade k's share of H1 2026 volume × (1 + g), spread evenly over the quarter's months and days. When the same-quarter trade figures on the 2026 basis are disclosed, they replace the group-times-share product. Growth g = 2%, ASSUMPTION, swept −5% to +5%, pending Hapag-Lloyd's stated outlook. Labels: seasonality DERIVED, g ASSUMPTION.

Volume classes per trade. Contract share 50% (ASSUMPTION, swept 30–70%), index-linked share 10% (ASSUMPTION), spot = the rest.

Contract cohorts (calendar ASSUMPTION). Asia–America: one cohort, 12-month term, reset 1 May each year. Other trades: 12 monthly cohorts of equal size, each with a 12-month term, reset on the first day of its month. Every cohort's rate at T0 is P0 of its trade (ASSUMPTION).

Non-USD invoicing (time-varying). Share n_k of each trade's revenue invoiced in currency C_k: Asia–Europe 5% EUR; Europe–America 5% EUR; Africa & Intraregional 5% EUR; Asia–America 3% CNY. All ASSUMPTION. The local-currency amount is set from USD at the T0 FX rate, the same in both passes (a tariff currency choice, not a conversion); the FX layer then converts it.

Collection lag (time-varying). One value for all trades, written DSO in formulas (profile key freight.collection_lag_days): trade receivables ÷ (latest-quarter group revenue ÷ days in quarter), DERIVED, about 48–50 days; the 30-day disclosed term is shown beside it.


### 6.4 Market inputs


| Trade | Series | Label | Material |
| :--- | :--- | :--- | :--- |
| Asia–Europe | WCI_SHA_RTM (WCI Shanghai–Rotterdam) | MARKET | Yes |
| Asia–America | WCI_SHA_LAX (WCI Shanghai–Los Angeles) | MARKET | Yes |
| Europe–America | WCI_RTM_NYC (WCI Rotterdam–New York) | MARKET | Yes |
| Africa & Intraregional | WCI_COMPOSITE | PROXY | Yes |


WCI values are USD/FEU; the module divides by 2 to get USD/TEU. At least 13 months of weekly history MUST be loaded so history replay and the reversal preset can look back 26 weeks. The dashboard headline is the average of the four index changes, weighted by H1 2026 trade volumes. SCFI is context only.


### 6.5 Calculation logic

Notation: trade k; shipment day d; ρ_k(d) = the index change since the anchor period, defined next. Here ρ is measured from the anchor: ρ_k(d) = I_k(d) ÷ I_k(anchor) − 1, where I_k(d) is the pass's index path (flat at I_k(T0) in the base pass, with dates before T0 at the T0 level). The base pass at T0 therefore carries the move since the anchor period, which reaches the baseline through the anchor adjustment; with the anchor snapshot it is zero (D35, D69).


| Volume class | Realised rate on shipment day d | Parameters (defaults, ASSUMPTION) |
| :--- | :--- | :--- |
| Spot | P0_k × (1 + β · ρ_k(d − L_p)) | β = 1.0; pricing lag L_p = 28 days (time-varying) |
| Index-linked | P0_k × (1 + κ · ρ_k(d − L_i)) | Clause pass-through κ = 0.8; clause lag L_i = 30 days |
| Contract cohort j, before its reset r_j | P0_k | Unchanged until reset |
| Contract cohort j, from r_j | P0_k × (1 + γ · ρ_k(r_j)) | γ = 0.5 (time-varying; applies at reset) |


The contract reset uses the index on the reset date with no further lag. Cohorts have 12-month terms, so each resets at most once in the horizon. β is never estimated against company-wide realised rates, which would dampen the shock twice.

Contract leakage (early repricing), both directions. For each cohort, gap(d) = spot rate(d) ÷ cohort rate(d) − 1.

Downside: on the first day gap < −g_down (default 30%), a share λ_down of the cohort's intact volume moves to the spot rate for the rest of its term, and a further share λ_loss is lost as volume (default 0%).

Upside: on the first day gap > g_up (default 30%), a share λ_up of the intact volume moves to the spot rate (carriers rolling contract cargo).

Defaults λ_down = λ_up = 10%, ASSUMPTION, run as a sensitivity sweep from 0% to 20% and never presented as an estimate. At most one leakage event per cohort per direction in the horizon. γ applies only to volume still intact at reset; leaked volume is never repriced twice.

Volume. V_k(d) = base daily volume × (1 + v_k · f(d)), where v_k is the volume shock and f the shape factor. Class shares apply after the volume shock.

Revenue and cash. Revenue earned on day d = Σ over classes of volume × realised rate. The share n_k becomes a line in C_k; the rest is USD. Cash for the share of revenue whose collection moves with transit time (freight.delay_affected_share, default 100%, ASSUMPTION, conservative, swept 0–100%) arrives on d + DSO + transit delay_k(d); the rest arrives on d + DSO. Prepaid freight invoiced at departure is not delayed by a longer voyage, and the split is not disclosed. The transit delay applies to shipments during the scenario's duration. The two shares are separate lines (sub_id suffix :arr and :dep), so both cash dates are explicit. One FREIGHT_REVENUE line is emitted per sub_id, currency and cash date, with sub_id freight:<class>:<trade>[:<cohort>]. Lines are labelled ASSUMPTION under the weakest-label rule (2.4), because class shares and cohorts are assumptions.

Volume lines. For each trade and shipment month: TEU shipped, both passes (2.6), after volume shocks and after volume lost through leakage (λ_loss), so bunker's MFR reads the reduced TEU.


### 6.6 Scenario parameters and presets

The freight block of ScenarioParams holds, per trade: rate_shock_pct, volume_shock_pct, transit_delay_days, optional shape, and an optional explicit rate_shock_path (weekly date and %), which overrides shock and shape. Leakage triggers and shares are profile parameters that a scenario may override (D7).


| Preset | Scope | Freight parameters |
| :--- | :--- | :--- |
| Demand slump (demand_slump) | Joint | All trades: rate −20%, volume −5%; ramp over 8 weeks; persistent |
| Red Sea re-escalation (red_sea_reescalation) | Joint | Asia–Europe: rate +40%, transit delay +12 days (single default for the 10–14 day range, D36); step; 182 days |
| Freight rate reversal (freight_rate_reversal) | Joint | Per trade: the index gives back 50% of its rise over the last 26 weeks, over 12 weeks: shock = −0.5 × (I(T0) − I(T0 − 26 weeks)) ÷ I(T0), only if that rise is positive; ramp 12 weeks; persistent |
| History replay (history_replay) | Freight | Re-runs a past window: rate_shock_path = I(start + (t − T0)) ÷ I(start) − 1 from loaded history, computed when the preset is built and stored in the parameters (D37) |
| Custom | Any | User-defined |


The reversal and replay presets compute their numbers from history when built; the numbers, not the formula, are stored, so replay never needs history.


### 6.7 Hypothetical hedge tool

None in v1. No freight derivatives are disclosed and the instrument is thin; the lever is the contract share, which the treasurer can vary through run overrides. Coverage is shown as the rate-locked share per trade, as information only.


### 6.8 Outputs

Line codes: FREIGHT_REVENUE (gross). No offset codes.

Volume lines: TEU per trade per month, both passes.

Dashboard metrics: index level per route (USD/TEU), week-on-week change, volatility regime, estimated realised rate per trade (volume-weighted across classes), revenue exposed over the next two quarters (spot, index-linked and cohorts resetting within two quarters), rate-locked share per trade, and the upside and downside of the active scenario.


### 6.9 Tier A alert rules


| Rule | Series | Warning | Severe | On severe |
| :--- | :--- | :--- | :--- | :--- |
| Route move | Each WCI route, weekly | Shared default: 10% or 2.5× vol | 20% or 4× vol | request_auto_build |
| Index–realised gap | Estimated realised rate vs index per trade, weekly change in the gap | 15 percentage points | 25 percentage points | none |


The realised rate in the gap rule is the model's estimate and is labelled so.


### 6.10 Agent-adjustable parameters


| Key | Type | Event types | Steps (small / medium / large) | Hard limits |
| :--- | :--- | :--- | :--- | :--- |
| freight.pricing_lag_days | Time-varying | trade_disruption, market_structure, company_announcement | 7 / 14 / 28 days | 0–90 |
| freight.gamma | Time-varying | contract_season, company_announcement | 0.10 / 0.20 / 0.30 | 0–1 |
| freight.leakage_gap_down, freight.leakage_gap_up | Time-varying | market_structure, trade_disruption | 0.05 / 0.10 / 0.15 | 0.10–0.60 |
| freight.leakage_share_down, freight.leakage_share_up | Time-varying | market_structure, competitor_action | 0.05 / 0.10 / 0.15 | 0–0.30 |
| freight.non_usd_share.<trade> | Time-varying | payment_rule, fx_policy, company_announcement | 0.02 / 0.05 / 0.10 | 0–0.30 |
| freight.delay_affected_share | Time-varying | trade_disruption, payment_rule, company_announcement | 0.10 / 0.25 / 0.50 | 0–1 |


All freight keys are step_or_direct; signs follow the event direction.


### 6.11 Notes for items not modelled

Demurrage and detention: not modelled. Revenue not assigned to trades, mainly demurrage and detention, was USD 1,065m in H1 2026, about 10% of Liner revenue. The treasurer judges its relevance to each event.

Fuel share of the base rate: about 15% of the all-in rate; freight results are overstated by roughly that share.

Terminal & Infrastructure revenue (USD 360m in H1 2026) is excluded.


### 6.12 Playbook


| Trigger | Actions |
| :--- | :--- |
| Sustained fall (route alert, demand slump or reversal run at HIGH or above) | Raise the liquidity buffer; secure or extend facilities; review discretionary payment timing; press collections |
| Spike (severe upward route alert) | Plan deployment of the surplus; schedule conversion with the FX workflow; prepare for reversal by running the reversal preset |
| Disruption (transit delay in any run) | Model slower transit and collections; check minimum-headroom dates against payment calendars |



### 6.13 Back-tests and sanity checks

H1 2026 trade-level bridge (DERIVED from disclosed tables): volume effect = Σ ΔV × prior rate = +107.0; rate effect = Σ ΔP × current volume = −11.9; sum +95.0 against the reported change in trade-assigned revenue, 9,395 − 9,297 = +98. Residual +3.0 MUST be within ±5 (PROPOSED ILLUSTRATIVE). Signs MUST match.

Calibration chart, no pass/fail: quarterly average WCI against the disclosed realised average rate for 6–8 quarters, to justify the default β, κ and pricing lag visually.

Unit checks: USD/FEU to USD/TEU; TTEU against TEU; trade volumes summing to the group total within rounding (6,683 against 6,684).

Boundary check: no line uses revenue not assigned to trades or Terminal & Infrastructure figures.


### 6.14 Worked example: Red Sea re-escalation, freight part

Fixture tests/fixtures/freight_example.yaml (USD m unless stated): T0 = 31 Oct 2026; Asia–Europe only; shipments in November 2026 only (30 days); 300,000 TEU, evenly 10,000 TEU per day; P0 = USD 1,400/TEU; classes: spot 40% (4,000 TEU/day), index-linked 10% (1,000/day), cohort A 25% (2,500/day, resets 15 Nov 2026), cohort B 25% (2,500/day, resets 1 Mar 2027); β = 1, L_p = 28 days, κ = 0.8, L_i = 30 days, γ = 0.5, g_up = 30%, λ_up = 10%; DSO = 49 days; delay_affected_share = 100%; no non-USD invoicing.

Scenario: Asia–Europe index +40%, step from 1 Nov 2026; transit delay +12 days.


| Shipment days | Spot | Index-linked | Cohort A | Cohort B | Revenue per day |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Base pass, 1–30 Nov | 1,400 | 1,400 | 1,400 | 1,400 | 14.00 |
| Scenario, 1–14 Nov | 1,400 (lag) | 1,400 (lag) | 1,400 | 1,400 | 14.00 |
| Scenario, 15–28 Nov | 1,400 (lag) | 1,400 (lag) | 1,680 after reset: 1,400 × (1 + 0.5 × 0.40) | 1,400 | 14.70 |
| Scenario, 29–30 Nov | 1,960 once the 28-day lag ends | 1,400 (lag) | 1,680 | 2,250 TEU at 1,400 + 250 TEU at 1,960 | 17.08 |


On 29 Nov the spot rate of 1,960 is 40% above cohort B's 1,400, beyond the 30% trigger, so 10% of cohort B leaks to spot. Against cohort A (1,680) the gap is 16.7%, so A does not leak. Index-linked volume stays at 1,400 because its 30-day lag ends after November.


| Result | Value |
| :--- | :--- |
| November revenue, base | 30 × 14.00 = 420.00 |
| November revenue, scenario | 14 × 14.00 + 14 × 14.70 + 2 × 17.08 = 435.96 |
| Revenue delta (opportunity) | +15.96 = cohort A 11.20 + spot 4.48 + leakage 0.28 |
| Realised average rate, scenario | 435.96m ÷ 300,000 = USD 1,453.20/TEU |
| Base cash dates | Shipment + 49 days: 20 Dec 2026 to 18 Jan 2027 |
| Scenario cash dates | Shipment + 12 + 49 days: 1 Jan to 30 Jan 2027 |
| Cumulative cash delta, 31 Dec 2026 | −168.00 (12 days of base collections, 1–12 Nov shipments, not yet received) |
| Cumulative cash delta, 18 Jan 2027 | −165.20 |
| Cumulative cash delta, 30 Jan 2027 | +15.96 |
| Gross adverse delta G_freight | 168.00 at 31 Dec 2026 |


The example shows the module's purpose: a rate rise that is an opportunity in revenue terms still creates a cash dip of USD 168m for a month, because the transit delay pushes collections out before the higher rates arrive. Volume lines: 300,000 TEU for November 2026 in both passes.

The cash dip scales with freight.delay_affected_share: at 50% the cumulative delta on 31 Dec 2026 is −84.00, and at 0% the transit delay moves no cash and only the +15.96 revenue gain remains.


### 6.15 Module acceptance tests

The worked example reproduces every figure above to the cent.

The H1 2026 bridge reproduces +107.0, −11.9 and a residual within ±5.

A zero-shock run gives delta 0 on every freight line.

Both directions: −40% on the fixture triggers downside leakage on cohort B once spot is 30% below it, and cohort A reprices to 1,120.

Contract volume is not repriced before its reset; leaked volume is not repriced at reset.

A shock to one trade leaves other trades' lines unchanged.

Non-USD share: a 5% EUR share on the fixture emits EUR lines whose USD value at T0 FX equals 5% of revenue, and an EUR move changes only the FX bar.

A volume shock reduces volume lines, and bunker reads the reduced TEU.

USD/FEU values are halved; a TEU/TTEU mix-up fails validation.

Reversal and replay presets store explicit numbers and replay without history.

With delay_affected_share = 0, a transit delay leaves every cash date unchanged; at 50%, the fixture's cumulative delta on 31 Dec 2026 is −84.00.


### 6.16 Open items

Defaults for γ, λ, leakage triggers, pricing lag, κ, index-linked share and volume growth g.

Verify the trade order and labels of the derived H1 rows against IR p. 10; once verified, relabel those rows DISCLOSED.

Confirm that the disclosed average freight rate is all-in, from the report's definition.

Confirm WCI route values are publicly available weekly at build time.

Same-quarter trade volumes on the 2026 basis for Q3 and Q4 (until disclosed, group volume × trade share is used).

MFR recovery share, agreed with bunker, needed for the v2 ex-fuel base rate.


## 7. Bunker module

Bunker turns VLSFO and HSFO price moves at two hubs, plus any consumption uplift, into dated fuel purchase payments and a change in MFR (Marine Fuel Recovery) collections. The position is treated as fully unhedged; a hypothetical hedge tool tests hedging policies.


### 7.1 Purpose and boundary

Owned: fuel purchase cash (all volumes, all priced at market); the change in MFR revenue caused by scenario price moves; settlement and margin cash of a tested hypothetical hedge.

Not owned: baseline MFR, which is already inside freight's all-in base rate; freight rate moves (freight); CO2 / EU ETS certificate cost (note only); LNG, biofuel and other grades (note only); FX (fuel is USD-priced, so bunker emits no non-USD lines in v1).

Committed volumes. A purchase commitment fixes the quantity, not the price. All volumes, committed or not, are priced at the market price of the purchase month and are fully exposed. Only fixed-price arrangements shield volume; none are disclosed, so in v1 the only shield is a hypothetical fixed-price supply contract tested in the hedge tool.


### 7.2 Evidence anchors


| Item | Value | Label | Source |
| :--- | :--- | :--- | :--- |
| Consumption; average price paid, H1 2026 | 2.4m t; USD 592/t | DISCLOSED | IR pp. 10–11 |
| Bunker and emissions expense, H1 2026 (H1 2025) | USD 1,540m (1,421m), of which CO2 certificates USD 112m (74m) | DISCLOSED | IR pp. 10–11 |
| Consumption; average price, FY2025 | 5.0m t; USD 528/t | DISCLOSED | AR p. 33 |
| H1 2026 consumption change | −3.5% | DISCLOSED | IR pp. 10–11 |
| Fuel hedges | None in 2025, none at present; derivatives only in exceptional cases | DISCLOSED | AR pp. 24, 46 |
| MFR | Offsets part of fuel price moves, up and down; extent depends on the market | DISCLOSED | AR p. 46 |
| Rotterdam MFO 0.5% FOB (context) | USD 504/t (Dec 2024), 372/t (Dec 2025), 540/t (30 Jun 2026) | DISCLOSED | AR p. 29; IR p. 7 |
| Brent (context) | USD 60.85 (31 Dec 2025), 72.92 (30 Jun 2026), +19.8% | DISCLOSED | AR p. 29; IR p. 7 |
| Fuel per TEU | 2.4m t ÷ 6,684 TTEU ≈ 0.359 t/TEU | DERIVED | IR pp. 2, 10–11 |
| Monthly consumption | 2.4m t ÷ 6 = 400,000 t | DERIVED | IR pp. 10–11 |



### 7.3 Exposure recipe


| Parameter | Default | Label | Type |
| :--- | :--- | :--- | :--- |
| Monthly consumption C0 | 400,000 t, changed only by scenario uplifts | DERIVED | Sizing |
| Hub weights | Singapore 60%, Rotterdam 40% | ASSUMPTION (Asia-heavy bunkering; swept) | Time-varying |
| Scrubber share s (HSFO share of consumption) | 15% | ASSUMPTION, modest rather than 0; replaced by a disclosed scrubber fleet figure if found | Time-varying |
| Supplier payment lag | 30 days | ASSUMPTION | Time-varying |
| MFR share σ; MFR lag L | 70%; 2 months | ASSUMPTION, σ swept 0–100% | Time-varying |
| Asia–Europe share of consumption α | 35% | ASSUMPTION | Sizing |


Purchases equal consumption each month (steady inventory) and are spread evenly over the days of the month. The exposure register holds one item per hub, grade and month, with sub_id bunker:purchase:<hub>:<grade>:<YYYY-MM>.


### 7.4 Market inputs


| Series | Use | Label | Material |
| :--- | :--- | :--- | :--- |
| VLSFO_SIN, VLSFO_RTM, HSFO_SIN, HSFO_RTM (weekly, USD/t, 380 cst for HSFO) | Base prices and scenario paths | MARKET | Yes |
| BRENT (daily, % change only) | Fill between weekly prints; β; divergence alert | MARKET; filled prices PROXY | No |


Base price (D38). The engine's base is the last weekly print at or before T0 (MARKET). Brent-filled daily prices are shown on the dashboard as indicative (PROXY) and are used in the engine only when a weekly print is stale (older than 10 days); then the run carries the proxy-based flag (D4).

Brent fill. Filled price = last print × (1 + β × Brent % change since that print). β = slope of weekly fuel % changes on weekly Brent % changes over the last 26 weeks, estimated separately for VLSFO and HSFO (hub-weighted), recalculated weekly, labelled MARKET with its formula (D25). With fewer than 13 weeks of data, β = 1.0, ASSUMPTION. β is shown on the dashboard.


### 7.5 Calculation logic

Blended price. At each hub h, B_h(t) = (1 − s) × VLSFO_h(t) + s × HSFO_h(t). Price paid P(t) = w_SIN × B_SIN(t) + w_RTM × B_RTM(t). VLSFO and HSFO are shocked separately: VLSFO_h scenario = VLSFO_h(T0) × (1 + shock_V × f(t)), and likewise for HSFO. There is no price-triggered switching between fuels; this is a fuel-mix effect, never called a natural hedge.

Monthly price and consumption. P_m = the average of P(t) over the days of month m. C_m = C0 × (1 + u × a × f_m), where u is the consumption uplift, a the affected share and f_m the average shape factor in month m.

Fuel purchases. On each day d of month m, purchases q(d) = C_m ÷ days in m, at P_m, paid on d + supplier lag. One FUEL_PURCHASE line per hub, grade and payment date (negative), labelled ASSUMPTION under the weakest-label rule (2.4), because hub weights and scrubber share are assumptions.

MFR change. The MFR base price P_base is the blended anchor price (H1 2026 average, 3.7), because baseline MFR sits inside freight's H1-level base rate. Reference price for month m: Ref_m = P_{m−L} from the pass's price path, with months before T0 at the T0 level (D35, D69).

\mathrm{MFR}_m = \sigma \times (\mathrm{Ref}_m - P_{base}) \times \text{fuel per TEU} \times \mathrm{TEU}_m

TEU_m comes from freight's volume lines for the same pass, by trade. With T0 prices equal to the anchor and no price shock, MFR_m is zero. Otherwise the base pass carries the MFR on the move since the anchor period, which reaches the baseline through the anchor adjustment; the scenario delta is the scenario-pass MFR minus the base-pass MFR. A demand slump reduces TEU and therefore recovery while fuel burn continues; this is intended.

MFR cash. MFR is billed with freight, so it is collected like freight: shipment day + DSO, plus the trade's transit delay on freight's delay-affected share (D16). One MFR_RECOVERY line per trade and cash date, labelled ASSUMPTION (σ and L are assumptions).

Documented approximation. Freight's base rate reflects the H1 2026 fuel level (about USD 592/t paid). The move from that level to today's hub prices enters the baseline through the anchor adjustment (3.7, D69). MFR uses a fixed fuel per TEU, so the extra burn from Cape routing is not recovered in the model.


### 7.6 Scenario parameters and presets

The bunker block holds vlsfo_shock_pct, hsfo_shock_pct, consumption_uplift_pct, uplift_affected_share and an optional shape. All values are PROPOSED ILLUSTRATIVE, ASSUMPTION.


| Preset | Scope | Bunker parameters |
| :--- | :--- | :--- |
| Red Sea re-escalation (red_sea_reescalation) | Joint | VLSFO +15%, HSFO +12%; consumption uplift +30% on the Asia–Europe share (α = 35%), for Cape of Good Hope routing; step; 182 days |
| Oil spike (oil_spike) | Joint | VLSFO +30%, HSFO +20% (VLSFO usually rises more, widening the spread); spike_revert over 26 weeks |
| Demand slump (demand_slump) | Joint | VLSFO −15%, HSFO −15%; consumption −3% on the full share (cancelled sailings: uplift −3% with affected share 100%, ASSUMPTION); ramp over 8 weeks; persistent |
| Bunker price reversal (bunker_price_reversal) | Bunker | Each grade gives back 50% of its rise over the last 26 weeks, over 12 weeks, computed like the freight reversal and stored as numbers |
| Custom | Any | Sliders for every field |



### 7.7 Hypothetical hedge tool

Targets: fuel_all (blended), vlsfo, hsfo. Coverage % of monthly consumption, tenor in months from the start date, strike K = blended (or grade) price at T0 by default.


| Instrument | Settlement | Margin | Trade-offs shown |
| :--- | :--- | :--- | :--- |
| Swap | Monthly HEDGE_SETTLEMENT = (P_m − K) × hedged tonnes, paid on the last day of month m (ASSUMPTION); positive when prices rise | Posted margin M(t) = max(0, −MTM(t)) × margin factor (1.0, ASSUMPTION), where MTM(t) = (P(t) − K) × remaining hedged tonnes (flat forward). MARGIN_CALL line on each date = −(M(t) − M(t − 1)) | Saving when prices rise; settlement and margin cash when prices fall; margin-eligible test |
| Fixed-price supply contract | HEDGE_SETTLEMENT = (P_m − K) × hedged tonnes, on the purchase payment dates | None | No margin cash, but less flexible and carries supplier credit risk (stated in the tool) |


Margin is one-sided (ASSUMPTION): the tool models collateral Hapag-Lloyd posts when a swap is out of the money, not collateral received when it is in the money. This is conservative, because incoming collateral is never counted as liquidity.

The tool runs as test_hedge (5.3): a pair with and without the hedge. Coverage appears only inside the tool; elsewhere the dashboard shows "Unhedged: 100% (no fuel hedges disclosed)".


### 7.8 Outputs

Line codes: FUEL_PURCHASE (gross); MFR_RECOVERY, HEDGE_SETTLEMENT, MARGIN_CALL (offset codes, excluded from G_bunker; margin has its own desk exception).

Inputs needed: TEU volume lines from freight, both passes.

Dashboard metrics: hub prices for both fuels, week-on-week change, volatility regime, VLSFO–HSFO spread, Brent–VLSFO gap and β, estimated fuel cash outflow for the next three months, upside and downside of the active scenario, and gross and net impact side by side with the label "net depends on the assumed MFR share".


### 7.9 Tier A alert rules


| Rule | Series | Warning | Severe | On severe |
| :--- | :--- | :--- | :--- | :--- |
| Hub price move | Each of the four fuel series, weekly | Shared default: 7.5% or 2.5× vol | 15% or 4× vol | request_auto_build |
| Early warning | Brent, daily | 4% | 7% | none |
| Spread move | VLSFO − HSFO at each hub, weekly change | USD 30/t | USD 60/t | none |
| Brent–VLSFO divergence | 4-week % change of blended VLSFO minus that of Brent | 10 percentage points | 20 percentage points | none |



### 7.10 Agent-adjustable parameters


| Key | Type | Event types | Steps (small / medium / large) | Hard limits |
| :--- | :--- | :--- | :--- | :--- |
| bunker.hub_weight_sin (Rotterdam = 1 − Singapore) | Time-varying | trade_disruption, port_disruption, sanction | 0.05 / 0.10 / 0.20 | 0.30–0.90 |
| bunker.scrubber_share | Time-varying | regulation, fleet_change, company_announcement | 0.02 / 0.05 / 0.10 | 0–0.40 |
| bunker.mfr_share | Time-varying | company_announcement, market_structure | 0.10 / 0.20 / 0.30 | 0–1 |
| bunker.mfr_lag_months | Time-varying | company_announcement | 1 / 2 / 3 months | 0–6 |
| bunker.supplier_lag_days | Time-varying | supplier_news, payment_rule | 7 / 14 / 21 days | 0–90 |


A new regulation restricting HSFO use with scrubbers, for example, maps to a large reduction of bunker.scrubber_share from the regulation's effective date.


### 7.11 Notes for items not modelled

CO2 / EU ETS cost not included. This module covers fuel purchase cost and MFR recovery only. The cost of CO2 emission certificates under the EU Emissions Trading System is excluded, although Hapag-Lloyd reports it together with bunker cost. Disclosed size: about USD 112m in H1 2026 (H1 2025: USD 74m). It is paid in EUR and rising as shipping's EU ETS coverage phases in. Treasurers should treat it as an additional, growing EUR outflow alongside fuel cost.

Other fuels: LNG, biomethane and biofuel use (AR pp. 11, 25) are not modelled.

Inventory: held steady; the disclosed fuel inventories (EUR 379.5m at 31 Dec 2025) are context only.

Grade proxy: whether "MFO 0.5% FOB Rotterdam" corresponds to VLSFO matters only for comparing with disclosed anchor prices.


### 7.12 Playbook


| Trigger | Actions |
| :--- | :--- |
| Sustained rise | Test a tactical hedge with the hedge tool; flag MFR recovery for commercial review; raise the liquidity buffer |
| Sharp fall | Plan for faster cash release; check margin exposure if a swap has been added |
| Red Sea | Run the joint preset with freight to see the net effect of higher rates, slower collections and higher burn |



### 7.13 Back-tests and sanity checks

BT2, H1 2026 cost level: 2.4m t × 592 = USD 1,420.8m against 1,540 − 112 = 1,428 (assumes the line holds only bunker and CO2 certificates). Difference −7.2 (−0.5%); tolerance ±29.6, the effect of rounding 2.4m t.

BT3, H1 price and consumption bridge: prior consumption = 2.4 ÷ (1 − 0.035) = 2.487m t. Price effect (592 − 542) × 2.4 = +120.0; volume effect (2.4 − 2.487) × 542 = −47.2; sum +72.8 against (1,540 − 112) − (1,421 − 74) = +81. Residual +8.2, within ±15 (PROPOSED ILLUSTRATIVE).

Quarterly chart, no pass/fail: 6–8 quarters of disclosed consumption and average price paid against the blended hub price, to support the hub weights, scrubber share and payment lag visually.

Divergence reference: Brent +19.8% against MFO +45.2% in H1 2026 (ratio 2.28); −18.5% against −26.2% in 2025 (ratio 1.42). Shown beside the divergence alert.


### 7.14 Worked example: Red Sea re-escalation, bunker part

Fixture tests/fixtures/bunker_example.yaml (USD m unless stated): T0 = 31 Oct 2026; purchases in November 2026 only (30 days); consumption C0 = 400,000 t; hub weights 60% Singapore, 40% Rotterdam; s = 20%; T0 prices (USD/t): VLSFO Singapore 600, VLSFO Rotterdam 580, HSFO Singapore 450, HSFO Rotterdam 430; supplier lag 30 days; σ = 70%; MFR lag 0 months in this fixture only, so the one-month window shows recovery; fuel per TEU 0.36 t; TEU from the freight fixture (300,000 TEU, Asia–Europe, November); transit delay 12 days; DSO 49 days.

Scenario: VLSFO +15%, HSFO +12%, consumption uplift +30% on α = 35%, step from 1 Nov 2026.


| Step | Base | Scenario |
| :--- | :--- | :--- |
| Blended Singapore | 0.8 × 600 + 0.2 × 450 = 570.00 | 0.8 × 690 + 0.2 × 504 = 652.80 |
| Blended Rotterdam | 0.8 × 580 + 0.2 × 430 = 550.00 | 0.8 × 667 + 0.2 × 481.60 = 629.92 |
| Price paid P | 0.6 × 570 + 0.4 × 550 = 562.00 | 0.6 × 652.80 + 0.4 × 629.92 = 643.648 |
| Consumption | 400,000 t | 400,000 × (1 + 0.30 × 0.35) = 442,000 t |
| November purchases | 224.800 | 284.492 |
| Daily payment, 1–30 Dec 2026 | −7.493 | −9.483 |



| Result | Value |
| :--- | :--- |
| Fuel purchase delta | −59.692 = price 400,000 × 81.648 = −32.659, plus uplift 42,000 × 643.648 = −27.033 |
| Fuel-mix effect | The blended price rises 14.53%, against 15.00% if s were 0 (P would go from 592.00 to 680.80) |
| MFR change for November | 0.70 × (643.648 − 562.00) × 0.36 × 300,000 = +6.173 |
| MFR cash dates | Shipment + 12 + 49 days: 1–30 Jan 2027, 0.206 per day |
| Net bunker delta | −59.692 + 6.173 = −53.519 |
| Gross adverse delta G_bunker | 59.692, reached on 30 Dec 2026 |
| MFR recovery as share of gross | 10.3% |


Fixture caveat. The freight fixture covers only Asia–Europe November volume (300,000 TEU, about 108,000 t of fuel at 0.36 t/TEU), while consumption is group-wide (442,000 t in the scenario). The 10.3% recovery ratio is an artefact of the fixture, not an estimate of MFR recovery, and MUST NOT be quoted as one.


### 7.15 Module acceptance tests

The worked example reproduces every figure above to the cent.

BT2 and BT3 reproduce within their tolerances, with signs.

A zero-shock run gives delta 0 and MFR change 0.

With s = 0, the price change equals the VLSFO shock exactly.

A price in USD/bbl is rejected; VLSFO and HSFO values are never swapped (grade check).

A demand slump with no price change gives zero MFR change; with a price change, lower TEU gives lower recovery.

Hedge tool, swap: a price rise gives a positive settlement equal to the purchase increase on hedged tonnes; a −20% fall gives negative settlements and margin calls, and the margin-eligible test runs.

Hedge tool, fixed-price supply: same settlements, no MARGIN_CALL lines.

With fewer than 13 weeks of history, β = 1.0 and is labelled ASSUMPTION.

A stale weekly print switches to the Brent fill and sets the proxy-based flag.

A scrubber-share adjustment effective on a future date changes the blended price only from that date.

The demand-slump preset lowers fuel purchases through both price and consumption, and lowers MFR collections (a negative MFR change), with freight's TEU fall shrinking that change.


### 7.16 Open items

Defaults for the MFR share and lag (also needed for freight's v2 base rate).

Hub weights, scrubber share and supplier payment lag.

Consumption uplift from Cape routing, and the Asia–Europe share of consumption.

Confirm that weekly VLSFO and HSFO prices for Singapore and Rotterdam are publicly reported.

Check whether the reports disclose a scrubber fleet figure, to set the default s.

Confirm whether "MFO 0.5% FOB Rotterdam" corresponds to VLSFO.


## 8. Interest rate module

Rates turns central-bank-driven rate paths and credit spread moves into dated interest cash on both sides of the balance sheet: floating debt and interest-bearing cash, plus refinancing of maturing debt and the ZIM acquisition tranche. Which direction is adverse depends on the net floating position, which changes over the horizon as maturities roll to floating debt and if the ZIM tranche is switched on. Debt and cash sides are therefore always calculated and shown separately, and the dashboard shows the net floating position by month.


### 8.1 Purpose and boundary

Owned: interest on floating, fixed and lease liabilities; interest income on cash; USD principal and drawdown lines for maturing debt; interest on refinanced debt; the switchable ZIM tranche; settlements and margin of a tested hypothetical swap. All lines are in their native currency (USD or EUR).

Not owned: EUR debt principal (FX module, 9B, from the same maturity schedule); FX conversion of EUR interest (9A); special fund fair-value effects, embedded bond options and covenants (notes only).

Effectively unhedged. The only disclosed interest rate swap (USD 2.2m nominal, 5.30% fixed) is under 0.1% of floating debt. The position is treated as unhedged; a hypothetical swap tool tests policies instead of a swap register.


### 8.2 Evidence anchors


| Item | Value | Label | Source |
| :--- | :--- | :--- | :--- |
| Variable-rate financial debt | EUR 1,999.1m (31 Dec 2025) | DISCLOSED | AR p. 217 |
| Financial debt: banks; bonds; other | EUR 1,528.0m; 301.4m; 920.7m (total 2,750.1m, DERIVED) | DISCLOSED | AR pp. 211, 213 |
| Lease liabilities | EUR 3,637.1m | DISCLOSED | AR pp. 211, 213 |
| Sustainability-linked bond | EUR 300m, 7 years, 2.5% coupon, +0.25 pp from 15 Oct 2025 if targets are missed (status not stated) | DISCLOSED | AR p. 211 |
| Weighted average nominal rate | 4.8% | DISCLOSED | AR p. 211 |
| Financial debt and leases; current portion | USD 7,153m; USD 1,852m (30 Jun 2026) | DISCLOSED | IR pp. 14–15 |
| Cash; money-market policy | USD 3,150m; invested up to six months | DISCLOSED | IR p. 15; AR p. 35 |
| Interest expense; money-market income; special fund income, H1 2026 | USD 236m; USD 69m; USD 41m | DISCLOSED | IR p. 9 |
| Interest expense FY2025, of which leases | EUR 380m; EUR 207.1m | DISCLOSED | AR pp. 32, 188 |
| Interest rate swap | USD 2.2m nominal, 5.30% fixed | DISCLOSED | AR p. 220 |
| ±100 bp sensitivity on variable debt (EBT) | −EUR 20.8m / +EUR 21.5m (2025); zero floor | DISCLOSED | AR p. 217 |
| Ratings | BB+ (S&P), Ba1 (Moody's) | DISCLOSED | AR p. 35 |
| ZIM acquisition financing | USD 4.2bn; terms not disclosed | DISCLOSED (amount) | IR p. 3 |



### 8.3 Exposure recipe


| Item | Recipe | Label | Type |
| :--- | :--- | :--- | :--- |
| Floating debt | EUR 1,999.1m converted at the 31 Dec 2025 closing rate (D12), split USD 70% / EUR 30%. Three equal tranches per currency, resetting quarterly, staggered one month apart (first resets in the three months after T0) | DISCLOSED total; split and tranches ASSUMPTION | Sizing |
| Floating spreads | USD: SOFR + 200 bp; EUR: €STR + 225 bp, where the extra 25 bp covers the gap between €STR and the actual loan benchmark | ASSUMPTION, checked in the calibration (8.13) | Sizing |
| Fixed debt | Total financial debt − floating debt, both at 31 Dec 2025 while maturities come from 30 Jun 2026 (a documented simplification, replaced when a report gives both on one date). EUR 300m bond at 2.5% (annual coupon on 15 Oct, ASSUMPTION; step-up status open). The rest in USD at 4.8% (the disclosed group weighted average, used as the fixed rate: ASSUMPTION), interest paid monthly in equal amounts | DERIVED amount; fixed rate, coupon date and payment pattern ASSUMPTION | Sizing |
| Leases | Separate fixed line; no rate effect in the horizon. If the disclosed lease repayment figure used in the baseline (3.7) already includes interest, the LEASE line is DELTA_ONLY so it is not added twice; otherwise it follows the interest_in_ocf flag (D51, open item). Interest = FY2025 lease interest (EUR 207.1m, converted) ÷ 12, monthly, USD, sub_id LEASE (D23) | DERIVED | Sizing |
| Maturities | Financial debt (excluding leases) due within 12 months, spread monthly (D1, D29). Each month's slice is taken pro rata from floating and fixed debt by their shares of financial debt (D44); its interest stops on the maturity date | DERIVED; split ASSUMPTION | Sizing |
| Interest-bearing cash | Cash at T0 × 85% (ASSUMPTION), split USD 80% / EUR 20% (ASSUMPTION). Three equal deposit cohorts per currency, rolling every 3 months, staggered one month apart. Yield = benchmark − 10 bp (ASSUMPTION). Balances are held constant (documented simplification) | ASSUMPTION | Sizing |
| ZIM tranche | USD 4.2bn, SOFR + 225 bp, drawn on 1 Jan 2027, quarterly resets, interest only (D24). Switch default off until the deal status is verified; results always shown both ways through run_pair | Amount DISCLOSED; terms ASSUMPTION | State |
| Central bank calendar | FOMC and ECB monetary policy meeting dates in the horizon, entered from the official calendars | DISCLOSED (official calendars) | Sizing |



### 8.4 Market inputs


| Series | Use | Material |
| :--- | :--- | :--- |
| SOFR (New York Fed) | USD benchmark for floating debt, deposits, new borrowing, ZIM, swap floating leg | Yes |
| ESTR (ECB) | EUR benchmark for floating debt and deposits | Yes |
| BB_OAS (FRED) | New-issue spread anchor | Yes when maturities or ZIM are in the horizon |
| UST_2Y, EA_2Y | Expectation alerts; hypothetical swap fixed rate | No |


All are public reference data, cited. The agentic part of this module is tracking central bank decisions through the news engine, not data retrieval.


### 8.5 Calculation logic

Rate paths. For currency c, benchmark b_c(t) = b_c(T0) + the cumulative sum of steps dated on or before t (2.7). Steps sit on central bank meeting dates in presets. The base pass is flat at b_c(T0) (4.2).

Floating tranches. On each reset date r_n, the rate is fixed at max(b_c(r_n), 0) + spread (the 0% floor binds only for EUR in practice; a binding floor is flagged). Interest for the period [r_n, r_{n+1}) is paid on r_{n+1}: notional × rate × days ÷ 360. The first payment after T0 covers the period fixed at the last reset before T0, at b_c(T0) + spread. Core timing rule: a rate change reaches cash only from each tranche's next reset.

Deposits. Each cohort's yield is fixed at its roll date, max(b_c(roll), 0) − 10 bp floored at 0, and interest is paid at the next roll: balance × yield × days ÷ 360.

Refinancing (D1). For each maturity slice M on date m:

DEBT_PRINCIPAL −M, in USD for the USD share (this module); the EUR share is emitted by FX (9B).

With refinancing_mode = rollover: DEBT_DRAWDOWN +M in USD on the same date. For the EUR share, the drawdown = EUR amount × USD per EUR at T0: a state value, identical in both passes, so the EUR repayment carries the FX exposure and the policy hedge offsets it (D49). The new debt pays SOFR + new-issue spread, resetting quarterly from drawdown; its interest lines are REFINANCING_COST.

New-issue spread = BB_OAS(T0) + premium (default 0 bp, ASSUMPTION) + credit_spread_change_bp in force on the drawdown date. Existing contractual spreads never change.

With refinancing_mode = unavailable: no drawdown and no refinancing interest; the principal is paid from cash.

Maturities in months 12–24 go to a watch list (amount, month, base and stressed new-issue rate), never to cash lines.

ZIM tranche. When on, interest lines like a floating USD tranche, with credit_spread_change_bp applied if drawn after T0.

Lines. INTEREST_EXPENSE (floating, fixed, lease, ZIM), INTEREST_INCOME, REFINANCING_COST, DEBT_PRINCIPAL (USD), DEBT_DRAWDOWN; sub_ids rates:<kind>:<currency>:<n>, for example rates:float:USD:2, rates:deposit:EUR:1, rates:refi:2027-03. Labels follow the weakest-label rule (2.4), so most lines are ASSUMPTION.

Metrics. Net floating position = interest-bearing cash − floating debt, including refinanced debt (− ZIM when on), reported for every month of the horizon, not only at T0. Horizon-adjusted fixed share = (fixed debt not maturing in the horizon + swapped notional) ÷ financial debt, with leases shown as a separate line beside it.


### 8.6 Scenario parameters and presets

The rates block holds rate_path per currency (dated steps in bp), credit_spread_change_bp, refinancing_mode (shock fields) and zim_tranche_on (state). All values are PROPOSED ILLUSTRATIVE, ASSUMPTION.


| Preset | Scope | Rates parameters |
| :--- | :--- | :--- |
| Fed cutting cycle (fed_cutting_cycle) | Joint | USD −25 bp at each of the next four FOMC meetings (−100 bp in total); EUR flat |
| Fed cuts, ECB holds (fed_cuts_ecb_holds) | Rates | USD −25 bp at the next two FOMC meetings; EUR flat. Because the base path is flat, a "hold" equals the baseline for that currency |
| Both hiking (both_hiking) | Rates | USD and EUR +25 bp at each of the next three meetings of their central bank |
| Spread widening (spread_widening) | Rates | credit_spread_change_bp +150 from T0; rollover |
| Credit squeeze (credit_squeeze) | Rates | credit_spread_change_bp +200; refinancing_mode = unavailable |
| Sensitivity (rates_up_100bp, rates_down_100bp) | Sensitivity family | Rates members: USD and EUR together ±100 bp step at T0, no other driver moved; comparable in direction with AR p. 217 |



### 8.7 Hypothetical swap tool

Targets: floating_debt_usd, floating_debt_eur (pay-fixed swaps, to fix debt cost) and deposits_usd, deposits_eur (receive-fixed swaps, to lock deposit yields). Coverage % of the target notional, tenor in months.

Fixed rate K: the 2-year government yield of the currency at T0 + swap spread (default 0 bp, ASSUMPTION) (D20). K is fixed at T0, so credit_spread_change_bp never affects it.

Settlement: quarterly on the target's reset dates, HEDGE_SETTLEMENT = notional × (b_c(reset) − K) × days ÷ 360 for pay-fixed (the sign reverses for receive-fixed).

Margin: MTM(t) ≈ (b_c(t) − K) × notional × remaining years for pay-fixed; posted margin M(t) = max(0, −MTM(t)); MARGIN_CALL = −(M(t) − M(t − 1)). One-sided and conservative, as in 7.7.

Shown: the new horizon-adjusted fixed share, the saving if rates rise, the extra cost if rates fall, settlement and margin cash by date, and the margin-eligible test.


### 8.8 Outputs

Gross codes: debt side (INTEREST_EXPENSE, REFINANCING_COST) and cash side (INTEREST_INCOME), each measured alone; G_rates is the larger adverse side (4.3).

Offset codes: HEDGE_SETTLEMENT, MARGIN_CALL. DEBT_PRINCIPAL and DEBT_DRAWDOWN are excluded from G_rates (they net to zero under rollover).

Dashboard metrics: SOFR, €STR, 2-year yields, BB spread, net floating position by month, horizon-adjusted fixed share (leases separate), 12-month interest cash path (expense, income, net), upcoming maturities and the 12–24 month watch list, ZIM on and off side by side, binding floor flag.


### 8.9 Tier A alert rules


| Rule | Series | Warning | Severe | On severe |
| :--- | :--- | :--- | :--- | :--- |
| Expectation shift | UST_2Y, EA_2Y, weekly | Shared default: 15 bp | 30 bp | request_auto_build |
| Credit spread | BB_OAS, weekly | Shared default: 25 bp | 50 bp | request_auto_build |
| Benchmark step | SOFR, ESTR, daily change | 10 bp | 20 bp | none (the news engine carries the decision itself) |


Central bank decisions and guidance arrive as news events (central_bank_decision), and a surprise can request an auto-built run through the signal router (section 10).


### 8.10 Agent-adjustable parameters


| Key | Type | Event types | Steps (small / medium / large) | Hard limits |
| :--- | :--- | :--- | :--- | :--- |
| rates.new_issue_premium_bp | Time-varying | financing_news, rating_change, credit_event | 25 / 50 / 100 bp | −100 to +400 bp |
| rates.interest_bearing_cash_share | Sizing | bank_failure, capital_control, company_announcement | 0.05 / 0.10 / 0.20 | 0.50–1.00 |
| rates.zim_tranche_on | State switch | financing_news, company_announcement | Switch only: set directly from a factual or quantitative signal | true / false |


Switch keys use value_mode: switch, which accepts a direct value from factual or quantitative signals; validate_adjustment treats them as a third mode beside step_only and step_or_direct.


### 8.11 Notes for items not modelled

Special fund: income is disclosed (USD 41m in H1 2026), but its fair-value effects (±50 bp ≈ EUR 13.8m) are non-cash; the fund's interest income is not modelled as rate-sensitive cash.

Covenants: equity, liquidity and loan-to-value covenants exist and were complied with; levels are not disclosed.

Embedded bond options: non-cash fair-value effects, excluded.

2024 sensitivity: the published 2024 ±100 bp figures do not reconcile with a simple recompute; they are not used.


### 8.12 Playbook


| Trigger | Actions |
| :--- | :--- |
| Rates rising | Test pay-fixed swaps with the swap tool; pre-fund or bring forward refinancing |
| Rates falling | Lengthen deposit terms to lock in yields (test receive-fixed); consider repaying floating debt with surplus cash |
| Spreads widening | Pre-fund maturities; check credit line availability; run the credit squeeze preset |



### 8.13 Back-tests and sanity checks

Static ±100 bp check (D10). A separate function computes 1,999.1 × 1% × 1 year = EUR 19.99m, with no reset lag and the zero floor, against the published −20.8 / +21.5. Signs MUST match (+100 bp lowers earnings); magnitudes MUST be within ±10% (PROPOSED ILLUSTRATIVE): −3.9% and −7.0%.

Calibration check. At T0 rates, the generated register's annualised interest expense (floating, fixed, leases) should be within ±25% of 2 × USD 236m, and annualised deposit income within ±30% of 2 × USD 69m. A miss is reported, not failed; adjust parameters in a fixed order: for deposit income, the deposit spread, then the currency split, then the interest-bearing share; for expense, the loan spreads.

Scope checks: every line carries a currency; reset dates fall before maturity; leases never appear in refinancing.


### 8.14 Worked example: Fed cutting cycle

Fixture tests/fixtures/rates_example.yaml (USD m): T0 = 31 Oct 2026, horizon to 31 Oct 2027; SOFR(T0) = 4.00%; USD only; no maturities, fixed debt, leases or ZIM.

Floating debt: three tranches of 300 at SOFR + 2.00%, quarterly resets: A on 15 Nov, B on 15 Dec 2026, C on 15 Jan 2027, each paying at its next reset.

Cash: three deposit cohorts of 500 at SOFR − 0.10%, rolling quarterly on 1 Dec 2026, 1 Jan and 1 Feb 2027.

Scenario: USD −25 bp at fixture meeting dates 10 Dec 2026, 28 Jan, 18 Mar and 29 Apr 2027 (cumulative −25, −50, −75, −100 bp).

Deltas are the scenario minus the base, notional × Δrate × days ÷ 360. Only periods fixed after a cut change.


| Tranche or cohort | Payment date | Period (days) | Δ rate fixed at start | Delta |
| :--- | :--- | :--- | :--- | :--- |
| Debt A | 15 May 2027 | 15 Feb–15 May (89) | −0.50% | +0.3708 |
| Debt A | 15 Aug 2027 | 15 May–15 Aug (92) | −1.00% | +0.7667 |
| Debt B | 15 Mar 2027 | 15 Dec–15 Mar (90) | −0.25% | +0.1875 |
| Debt B | 15 Jun 2027 | 15 Mar–15 Jun (92) | −0.50% | +0.3833 |
| Debt B | 15 Sep 2027 | 15 Jun–15 Sep (92) | −1.00% | +0.7667 |
| Debt C | 15 Apr 2027 | 15 Jan–15 Apr (90) | −0.25% | +0.1875 |
| Debt C | 15 Jul 2027 | 15 Apr–15 Jul (91) | −0.75% | +0.5688 |
| Debt C | 15 Oct 2027 | 15 Jul–15 Oct (92) | −1.00% | +0.7667 |
| Cash 1 | 1 Jun 2027 | 1 Mar–1 Jun (92) | −0.50% | −0.6389 |
| Cash 1 | 1 Sep 2027 | 1 Jun–1 Sep (92) | −1.00% | −1.2778 |
| Cash 2 | 1 Apr 2027 | 1 Jan–1 Apr (90) | −0.25% | −0.3125 |
| Cash 2 | 1 Jul 2027 | 1 Apr–1 Jul (91) | −0.75% | −0.9479 |
| Cash 2 | 1 Oct 2027 | 1 Jul–1 Oct (92) | −1.00% | −1.2778 |
| Cash 3 | 1 May 2027 | 1 Feb–1 May (89) | −0.50% | −0.6181 |
| Cash 3 | 1 Aug 2027 | 1 May–1 Aug (92) | −1.00% | −1.2778 |



| Result | Value |
| :--- | :--- |
| Debt side saving | +1,439.25 ÷ 360 = +3.9979 |
| Cash side income lost | −2,286.25 ÷ 360 = −6.3507 |
| Net rates delta | −847 ÷ 360 = −2.3528 (falling rates are net adverse in this fixture, which has no maturities or ZIM) |
| First cash effect | 15 Mar 2027, more than three months after the first cut, because of reset timing |
| Gross adverse delta G_rates | 6.3507 (cash side), reached on 1 Oct 2027 |


Payments in the first months (15 Nov 2026 to 1 Feb 2027) are unchanged because their rates were fixed before the first cut. The last periods (Debt A from 15 Aug, Cash 3 from 1 Aug) pay after the horizon and do not appear.


### 8.15 Module acceptance tests

The worked example reproduces every figure above to four decimals.

The static ±100 bp check passes on signs and the ±10% tolerance.

A zero-shock run gives delta 0; a shock dated after a tranche's last reset in the horizon gives 0 for that tranche.

Fixed debt and leases show no delta; lease interest appears only as the fixed LEASE line.

Rollover: principal and drawdown net to zero in USD; refinancing_mode = unavailable removes drawdown and refinancing interest.

Spread widening changes only refinancing and post-T0 ZIM interest, never existing tranches.

ZIM: run_pair with the switch on and off returns both results; the switch never appears as a scenario delta.

Swap tool: pay-fixed saves cash when rates rise and costs cash when they fall; the margin test runs.

An EUR benchmark below zero fixes at 0% and raises the binding-floor flag.

EUR interest lines are emitted in EUR and converted only by 9A.

Under rollover with maturities in the horizon, the monthly net floating position falls as fixed maturities roll to floating debt, and with ZIM on it falls by a further USD 4.2bn from the draw date.


### 8.16 Open items

USD/EUR split of floating debt, loan spreads and reset frequency.

Interest-bearing share of cash, deposit currency split, deposit spread and average term.

Whether the EUR 1,999.1m floating figure includes leases; the current lease liabilities inside USD 1,852m.

ZIM financing: drawn, committed or proposed; amount drawn, timing, benchmark and spread.

How the new-issue spread relates to the BB index (premium default 0 bp).

Bond coupon date and whether the 0.25 pp step-up applies.

FOMC and ECB meeting dates for the horizon.

Confirm series IDs and access for NY Fed SOFR, FRED DGS2 and BB OAS, and the ECB 2-year yield curve series.

Whether the disclosed lease repayment figure includes interest (AR cash flow statement); this sets the LEASE line's treatment (D51).


## 9. FX: conversion layer and FX module

FX has two separate jobs. 9A, the conversion layer, is part of the shared engine: it converts every non-USD line from all four workflows once, applies the FX policy hedges, and reports net positions. 9B, the FX module, owns only the currency flows no other module owns. FX also covers the brief's emerging-market requirement through INR, BRL and CNY.


## 9A. FX conversion layer (engine)


### 9A.1 Inputs and outputs


| Inputs | Outputs |
| :--- | :--- |
| All cash lines from both passes, after date snapping | Every line with fx_rate_base, fx_rate_scenario, amount_usd_base, amount_usd_scenario filled |
| Base and scenario FX paths per currency (4.2) | FX policy hedge settlement lines (HEDGE_SETTLEMENT, owner fx, USD), both passes |
| Policy hedge definitions from 9B (state) | Net position per currency per date (reporting) |
| Translation memo positions from 9B | Translation memo results (not on the path) |



### 9A.2 Conversion

One conversion function, to_usd(amount, currency, date, path), is used everywhere. Rates are stored as USD per one unit of foreign currency (3.6), so conversion is always a multiplication; USD lines take rate 1.0.

Every line, from either pass, gets both rates for its date and both converted amounts (D46). Deltas and attribution use the base line's amount_usd_base and the scenario line's amount_usd_scenario (4.1, 4.3). No line may carry a defaulted rate. A stale rate uses the carried-forward value and makes the run INCOMPLETE (4.6); the run stops with an error only if a currency used by any line has no stored value at all.


### 9A.3 Policy hedges

Policy hedges are forwards generated from the policies in 9B, never from a trade register. Each hedge contract has: currency, direction (buy foreign), notional in foreign currency, value date (the hedged line's date), contract rate K and start date.

Sizing (D18): each policy is sized on its own exposure lines: hedge ratio × the native amount of the hedged lines. Netting never sizes a policy hedge.

Contract rate: forward points are zero, so K = spot on the start date. Contracts started on or before T0 take K from loaded history (or the stated locked rate). Contracts started after T0 take K from the path of the pass being run (D45), so a rolling programme delays an FX move rather than removing it.

Settlement: on the value date, HEDGE_SETTLEMENT = notional × (S(value date) − K) in USD, positive when the foreign currency has risen. It is emitted in both passes, each with its own path. The hedged exposure line stays gross and is converted at spot like any other line.

Attribution: policy hedge settlements are FX-owned lines, so their deltas fall in the FX bar (4.3).


### 9A.4 Netting and net positions (reporting only)

For each currency and date, the layer sums the native amounts of all lines (all owners) and the hedged notional, giving gross outflows, gross inflows, hedged amount and unhedged net. Netting across entities is an ASSUMPTION justified by Hapag-Lloyd's centrally managed treasury (AR p. 218). Because conversion is linear, netting never changes USD totals; it exists for reporting (the "net EUR need" view) and for the dashboard's 13-week exposure table.


### 9A.5 Translation memo and display

Translation memo: CNY and GBP net positions (9B) are revalued as position × (scenario rate at horizon end ÷ base rate − 1), in USD, and shown in the FX panel only. They never enter the liquidity path (D19).

Display currency: results may be shown in EUR for display only, converted once at the very end at the base USD/EUR rate for both paths (D47), so the display conversion never creates an apparent effect.


## 9B. FX module (own exposures)


### 9B.1 Purpose and boundary

Owned: EUR and CAD operating costs, the EUR dividend, EUR debt principal, INR and BRL transaction obligations, the CNY and GBP translation memo positions, and the definitions of the policy hedges.

Not owned: EUR interest (rates), non-USD freight inflows (freight), conversion and hedge application (9A). CO2 certificates are an EUR outflow noted only (7.11).


| Currency | Exposures |
| :--- | :--- |
| EUR | Operating costs, dividend, debt principal (this module); debt interest (rates); freight inflows (freight); CO2 certificates (note only) |
| CAD | Operating costs |
| INR, BRL | Transaction obligations |
| CNY | Translation memo position; freight inflows on Asia–America (freight). The two are separate: the memo never includes freight flows and is never netted with them |
| GBP | Translation memo position |



### 9B.2 Evidence anchors


| Item | Value | Label | Source |
| :--- | :--- | :--- | :--- |
| CAD operating costs | Hedged with 13-week forwards, up to 80% | DISCLOSED | AR p. 215 |
| CAD forward notional; average rate | CAD 57.5m; 0.72 (31 Dec 2025) | DISCLOSED | AR p. 220 |
| EUR debt repayment | Hedged up to 100%, natural hedges and forwards under one year | DISCLOSED | AR p. 215 |
| INR and BRL purchase-price obligations | INR 1.4bn; BRL 256m, hedged with transaction forwards | DISCLOSED | AR p. 215 |
| Posted foreign-currency exposure | USD 379.1m (31 Dec 2025) | DISCLOSED | AR p. 216 |
| ±10% USD sensitivity (earnings) | EUR +4.0 / −4.0; CNY −8.5 / +8.5; GBP +2.4 / −2.4 (USD m, USD +10% / −10%) | DISCLOSED | AR p. 216 |
| Implied net positions | EUR short ≈ USD 40m; CNY long ≈ USD 85m; GBP short ≈ USD 24m | DERIVED | From the sensitivity |
| Exchange-rate losses and gains, H1 2026 (context) | USD 237m; USD 244m | DISCLOSED | IR p. 9 |
| Treasury organisation | Liquidity managed centrally | DISCLOSED | AR p. 218 |



### 9B.3 Exposure recipe


| Item | Recipe | Label | Type |
| :--- | :--- | :--- | :--- |
| CAD operating costs | At least CAD 72m per 13 weeks (57.5 ÷ 0.80 = 71.9), spread weekly: CAD 5.54m per week, paid Fridays | DERIVED | Sizing |
| EUR operating costs | EUR 350m per quarter, spread weekly | ASSUMPTION, swept | Sizing |
| EUR dividend | The latest disclosed dividend as reference; the FY2026 dividend payable in 2027 equals it unless a stated dividend policy gives an estimate. Paid on 10 May 2027 | Reference DISCLOSED (to enter); estimate and date ASSUMPTION | Sizing |
| EUR debt principal | The EUR share of the monthly maturity slices (8.3), as DEBT_PRINCIPAL in EUR | DERIVED | Sizing |
| INR and BRL obligations | INR 1.4bn and BRL 256m, paid in 12 equal monthly amounts on the last day of each horizon month; switch fx.inr_brl_obligations_on (default on) because they may already be settled | Amounts DISCLOSED; dates ASSUMPTION | Sizing; switch is state |
| CNY and GBP memo positions | USD 85m long CNY and USD 24m short GBP, converted to native at T0 rates | DERIVED | Sizing |


Lines: FX_OPERATING_COST, DIVIDEND, DEBT_PRINCIPAL, FX_TRANSACTION (all native, negative); sub_ids fx:<kind>:<currency>:<date or n>.


### 9B.4 Policy hedges (definitions passed to 9A)


| Policy | Hedged lines | Ratio | Contract rate | Label |
| :--- | :--- | :--- | :--- | :--- |
| CAD rolling programme | Each weekly CAD cost, hedged 13 weeks before payment | 70% (cap 80%; above the cap fails validation) | Spot on the start date | Ratio ASSUMPTION; cap DISCLOSED |
| EUR debt principal | EUR DEBT_PRINCIPAL lines | 100% | Spot at T0 (forwards under one year) | ASSUMPTION within the disclosed 100% |
| INR and BRL obligations | FX_TRANSACTION lines | 100% | Locked at the 31 Dec 2025 rate | ASSUMPTION |
| EUR operating costs; EUR dividend | FX_OPERATING_COST (EUR); DIVIDEND | 0% by default | Spot at the start date | ASSUMPTION |


Changing a ratio is this module's hypothetical hedge tool: test_hedge with target cad_operating_costs, eur_operating_costs, eur_dividend, eur_debt_principal or inr_brl_obligations runs the pair with and without the change. The dashboard shows the policy hedge ratio per currency.


### 9B.5 Market inputs

ECB reference rates for EUR, CAD, CNY, GBP, INR and BRL, loaded with 1–2 years of history through the API and stored as USD per unit (3.6). Frankfurter is a fallback only. All six are material. The agentic part of this module is the news engine tracking currency policy and capital-control events.


### 9B.6 Scenario parameters and presets

The FX block holds move_pct per currency against USD, applied to USD per one foreign unit, with an optional shape per currency. All values are PROPOSED ILLUSTRATIVE, ASSUMPTION.


| Preset | Scope | FX parameters |
| :--- | :--- | :--- |
| EUR rally (eur_rally) | Joint | EUR +10%, GBP +5%, CAD +3% (broad USD weakness led by EUR); step; persistent |
| Emerging-market stress (em_stress) | Joint | INR −15%, BRL −15%; step; persistent |
| Broad USD strengthening (broad_usd_strengthening) | FX | EUR, CAD, GBP −8%; CNY −4%; INR, BRL −10%; step |
| CNY devaluation (cny_devaluation) | FX | CNY −10%; step |
| Sensitivity (fx_<ccy>_up_10, fx_<ccy>_down_10; IDs in 2.7) | Sensitivity family | FX members: one currency at a time, ±10% on USD per unit, no other driver moved |



### 9B.7 Outputs

Gross codes: the FX conversion effect before policy hedges (4.3). Offset codes: HEDGE_SETTLEMENT (policy hedges).

Dashboard metrics: rates and daily changes, volatility regime per currency, net exposure per currency over the next 13 weeks (from 9A.4), hedge ratio per currency, translation memo results, and the sign check status.


### 9B.8 Tier A alert rules


| Rule | Series | Warning | Severe | On severe |
| :--- | :--- | :--- | :--- | :--- |
| Rate move | EUR, CAD, GBP daily | Shared default: 1.0% or 2.5× vol | 2.0% or 4× vol | request_auto_build |
| Rate move, emerging markets | INR, BRL, CNY daily | 0.75% or 2.5× vol | 1.5% or 4× vol | request_auto_build |
| Volatility regime | Each currency | Moves into elevated | Moves into stressed | none |


Central bank actions and capital-control or repatriation rule changes arrive through the news engine (fx_policy, capital_control).


### 9B.9 Agent-adjustable parameters


| Key | Type | Event types | Steps (small / medium / large) | Hard limits |
| :--- | :--- | :--- | :--- | :--- |
| fx.hedge_ratio.cad | Time-varying | company_announcement, regulation | 0.05 / 0.10 / 0.20 | 0–0.80 |
| fx.hedge_ratio.eur_debt | Time-varying | company_announcement | 0.10 / 0.25 / 0.50 | 0–1 |
| fx.hedge_ratio.inr_brl | Time-varying | company_announcement, capital_control | 0.10 / 0.25 / 0.50 | 0–1 |
| fx.obligation_shift_months | Sizing | company_announcement, capital_control, payment_rule | 1 / 3 / 6 months | 0–24 |
| fx.inr_brl_obligations_on | State switch | company_announcement | Switch only | true / false |


Hedge-ratio adjustments record a change in the company's actual hedging (3.3); recommended changes go through test_hedge and the recommendation agent.


### 9B.10 Notes for items not modelled

Capital controls and repatriation (CNY, INR, BRL): qualitative only, through the news engine; the CNY position makes this relevant.

EUR reporting translation: non-cash, excluded.

H1 2026 exchange-rate losses and gains (USD 237m and 244m): context only.

Forward points: ignored (spot held flat), a documented simplification.


### 9B.11 Playbook


| Trigger | Actions |
| :--- | :--- |
| EUR rally | Raise the EUR hedge ratio for operating costs and the dividend (test with test_hedge); buy EUR earlier |
| Emerging-market stress | Review the hedge on INR and BRL obligations; minimise cash held in those currencies |
| Volatility spike | Shorten the review cycle; check that the CAD programme is within its 80% cap |



### 9B.12 Back-tests and sanity checks

Sign check (D11). Feed the implied positions through to_usd: EUR −40, CNY +85, GBP −24 (USD m at T0). A −10% move in USD per foreign unit gives +4.0, −8.5 and +2.4, matching AR p. 216; +10% gives the reverse. Signs and amounts MUST match exactly.

CAD sizing check: 57.5 ÷ 72 = 79.9%, within the 80% cap.

Storage check: every stored FX value is USD per unit; a value outside a plausible band for its currency fails at load.


### 9B.13 Worked example: EUR rally

Fixture tests/fixtures/fx_example.yaml (USD m unless stated): T0 = 31 Oct 2026; base USD per EUR 1.15, USD per CAD 0.72. Scenario: the EUR rally preset's EUR +10% (1.265) and CAD +3% (0.7416), step from 1 Nov 2026. Lines (native):


| Line | Owner | Date | Native | Hedge |
| :--- | :--- | :--- | :--- | :--- |
| EUR operating cost | fx | 30 Nov 2026 | −30.00 EUR | None |
| EUR dividend (fixture date) | fx | 15 Dec 2026 | −50.00 EUR | None |
| EUR interest | rates | 15 Dec 2026 | −2.00 EUR | None |
| EUR freight inflow | freight | 15 Dec 2026 | +12.00 EUR | None |
| EUR debt principal | fx | 20 Dec 2026 | −20.00 EUR | 100% at K = 1.15 |
| CAD operating cost | fx | 30 Nov 2026 | −13.00 CAD | 70% (9.1 CAD), started before T0 at K = 0.71 |
| CAD operating cost | fx | 28 Feb 2027 | −13.00 CAD | 70% (9.1 CAD), started 29 Nov 2026 at that pass's spot |



| Line | Base USD | Scenario USD | Delta |
| :--- | :--- | :--- | :--- |
| EUR operating cost | −34.50 | −37.95 | −3.45 |
| EUR dividend | −57.50 | −63.25 | −5.75 |
| EUR interest (rates) | −2.30 | −2.53 | −0.23 |
| EUR freight inflow (freight) | +13.80 | +15.18 | +1.38 |
| EUR debt principal | −23.00 | −25.30 | −2.30 |
| CAD operating cost, 30 Nov | −9.36 | −9.6408 | −0.2808 |
| CAD operating cost, 28 Feb | −9.36 | −9.6408 | −0.2808 |
| Hedge: EUR principal, 20 × (S − 1.15) | 0.00 | +2.30 | +2.30 |
| Hedge: CAD 30 Nov, 9.1 × (S − 0.71) | +0.091 | +0.28756 | +0.19656 |
| Hedge: CAD 28 Feb, 9.1 × (S − K at 29 Nov) | 0.00 (K = 0.72) | 0.00 (K = 0.7416) | 0.00 |
| Total |  |  | −8.41504 |



| Result | Value |
| :--- | :--- |
| Attribution | FX bar −8.41504; freight, bunker and rates bars 0.00, even though freight's inflow and rates' interest moved, because only their USD value changed |
| Net EUR need on 15 Dec 2026 (9A.4) | −50 − 2 + 12 = −40.00 EUR, unhedged |
| Unhedged EUR in the window | 30 + 40 = 70.00 EUR; 70 × 0.115 = −8.05, equal to the four unhedged EUR deltas |
| EUR principal | Fully hedged: −2.30 + 2.30 = 0 |
| CAD rolling programme | The hedge started before T0 offsets 70% of the November move; the hedge started after the shock locks the new rate and offsets nothing |



### 9B.14 Module acceptance tests

The worked example reproduces every figure above to five decimals.

The sign check reproduces +4.0, −8.5, +2.4 and their reverses exactly.

A CAD hedge ratio above 80% fails validation.

Every non-USD line carries both FX rates and both converted amounts; none is defaulted.

Netting changes no USD total: line-by-line and net conversion agree to the cent.

Translation memo positions never appear on the liquidity path.

A zero-shock run gives an FX bar of exactly 0.

With fx.inr_brl_obligations_on = false, no INR or BRL lines or hedges are emitted.

Display conversion to EUR changes no result.

EUR rally plus an EUR maturity under rollover: with a 100% hedge the net delta of principal, drawdown and hedge is 0; at 0% it is −(move × EUR amount × T0 rate).


### 9B.15 Open items

Size of EUR operating costs; the EUR dividend reference amount, estimate and payment date.

Actual CAD hedge ratio (the default sits below the 80% cap).

INR and BRL payment dates, whether the obligations are already settled, and what they are for (D34).

Whether netting across entities is acceptable (centralised treasury assumption).

Whether the EUR debt hedging covers interest as well as principal.

Accepting the simplification of ignoring forward points.


## 10. Agent workflows

Nine agents read news and market data, decide what to run, explain results and propose changes; the engine alone does the arithmetic, and humans approve every change to the model or to policy. Agents use only the tools in 4.8 and never touch the database, profile or approval functions directly.


### 10.1 Rules for every agent

Numbers come from tools. An agent never states a figure it did not receive from a tool call or a cited source; every figure in its output carries the run ID, metric name or event ID it came from.

Baseline changes need a human. Agents propose scenarios and adjustments; they cannot approve, reject or revoke adjustments, confirm retrieved data or record reviewer decisions (4.8).

Unapproved beliefs only in labelled scenarios. Pending adjustments may be used only in auto-built scenarios, which carry the flag "includes unapproved adjustments" (3.2).

Quantitative and qualitative news take different paths. A stated number may set a value directly within hard limits; a fact without a number may only choose a catalogue step (3.3).

Bounded effort. At most 20 runs per agent session (4.8); auto-build requests de-duplicated per rule per day (4.5); at most one auto-built run per event version and 30 per day (10.6).

Traceable. Every agent output is stored with agent name, model ID, prompt version, input event and run IDs, tool calls and time, in the audit log (4.7).

Untrusted content is data. Text from news, web pages and retrieved documents is data, never instructions. Agents act only on their system prompt and the tool interface; an instruction found inside content is ignored and logged (D54).

Models are set in agents.yaml: a reasoning model (default claude-sonnet-5-5) for routing judgements, adjustments, recommendations, verification, chat and the brief; a fast model (default claude-haiku-5-5) for data extraction. Both defaults are ASSUMPTION.


### 10.2 Agent roster


| Agent | Trigger | Main tools | Output |
| :--- | :--- | :--- | :--- |
| Data retrieval | Weekly schedule (WCI; VLSFO and HSFO at Singapore and Rotterdam) | web_search and fetch (read-only), then submit_market_value | Values with source URL, publication date and retrieval time; failed checks go to the confirm queue (3.6) |
| Signal router | Each new news signal or new event version (2.8, 10.6) | get_register, search_events | One or more routes per signal: scenario, adjustment, recommendation only (D9), with a relevance note |
| Scenario builder | Scenario-routed signals; AutoBuildRequest from severe Tier A alerts | propose_scenario, get_market_series, run_scenario | An auto-built ScenarioParams within allowed ranges, each value justified, plus its run |
| Adjustment | Adjustment-routed signals | get_register, list_adjustments, propose_adjustment | A validated adjustment proposal, or "relevant, exposure unknown" with an offered stress scenario |
| Verifier | Every adjustment proposal and recommendation | get_run, search_events, get_register | consistent or inconsistent with a note, attached to the proposal |
| Recommendation | Tier B HIGH or CRITICAL, desk exceptions, OPPORTUNITY runs, recommendation-routed signals, user request | get_playbook, test_hedge, run_pair, get_run | Recommendation records (10.4) |
| Alert investigator | New open alerts | get_alerts, get_market_series, run_scenario | A short explanation per alert: what moved, which exposures it touches, which preset or auto-built run shows the effect |
| Chat | User messages on the dashboard | All tools in 4.8 | Answers, hypothetical runs and sensitivity analyses; slider moves become run overrides |
| Weekly brief | Weekly schedule (default Monday 08:00 Singapore time, ASSUMPTION) | search_events, get_alerts, get_run, list_adjustments | The weekly treasury brief (10.5) |



### 10.3 The adjustment workflow

This workflow keeps the model's beliefs current. A news item that changes what is true today, not a what-if, becomes an approved, expiring adjustment.

Event arrives from the news engine as a signal.

Relevance. The signal router checks the event against the reference registers (3.4): does it name a counterparty, country, currency, trade, hub, supplier or regulation that touches the company? Irrelevant events are logged and dropped from this path.

Mapping. The adjustment agent finds the catalogue keys whose event types match (3.3). Quantitative signals may propose a direct value; qualitative signals choose a step (small, medium, large). The agent sets an effective date from the event (for example a regulation's start date) and a review date within max_review_days.

Validation. propose_adjustment runs validate_adjustment; rejected proposals return their errors and are never queued.

Unknown exposure. When the event is relevant but the registers hold no matching entry (for example a failed bank not in the counterparty register), the agent proposes nothing to the baseline, flags "relevant, exposure unknown", and the scenario builder offers a stress scenario instead.

Verification. The verifier checks that the proposal follows from the cited evidence and registers.

Human decision. The proposal appears in the approval queue with old and new values, step, dates, evidence links, justification, confidence and the verifier's result. A named user approves or rejects it; approval may shorten the review date but never changes the value.

In force. Approved adjustments apply to every run from their effective date until their review date or revocation, and every run records which were active. Expired adjustments drop out automatically and appear in the next weekly brief for re-approval or removal.

Worked illustration: a bank failure. The router matches the bank in the counterparty register; the adjustment agent proposes liquidity.T2.<bank> and liquidity.T1.<bank> availability 0 (large step) from the failure date; once approved, the bank's credit line share and deposits leave the counted tiers (3.7, D30). Because the register is mostly ASSUMPTION, the dashboard says the demo runs on an assumed register.


### 10.4 Recommendation records


| Field | Contents |
| :--- | :--- |
| recommendation_id, created_at | Identity |
| trigger | Alert ID, run ID or event ID that prompted it |
| lever | Hedge ratio change, hypothetical hedge, liquidity buffer, facility action, payment timing, collections, deposit term, conversion timing, or a commercial review flag |
| evidence_runs | The run_pair or test_hedge comparison showing the effect, with run IDs |
| expected_effect | Change in minimum headroom, the relevant driver bar and cost in the favourable direction, taken from the runs |
| playbook_refs | Module playbook entries used |
| risks_and_limits | What the recommendation does not cover, and the labels of the inputs it relies on |
| verifier_result | From the verifier |
| status | proposed, accepted, rejected, deferred; with decider, time and rationale |


A recommendation never executes anything; accepting it records a decision only.


### 10.5 The weekly brief

The brief answers the project brief's three questions, in this order, for the past week:

What changed: news events by category (regulatory, economic, industry), with sources; market moves and Tier A alerts; new, expiring and expired adjustments; data status (stale series, unconfirmed values); the change in 12-month minimum headroom by driver compared with prices held at last week's levels (4.10).

Why it matters: the impact pathway for each material item (which driver, which exposures, which dates), quantified from the latest scheduled runs (4.10), with their labels and flags.

What to do next: open recommendations with their evidence, playbook actions, and items awaiting human approval.

The brief is stored with its run and event IDs and can be exported as Markdown or PDF. Regulatory topics that are not modelled (sanctions, AML/KYC, payment rules, tax, capital controls) appear in sections 1 and 3 through the news engine and the reference registers (D27).

If the news engine produces its own weekly report, its sections feed "What changed", and this brief adds "Why it matters" and "What to do next", quantified from runs, so the treasurer never receives two competing briefs (D68). The news engine's report, if provided, has the format {"week_start", "week_end", "sections": [{"category": "regulatory|economic|industry", "items": [{"event_id", "text"}]}]}. If none is provided, the brief builds "What changed" from that week's signals.


### 10.6 News engine interface

The news engine is built separately. Its only contract is the signal format in 2.8 (D58).

Delivery. submit_signal(signal or list of signals) is the only delivery method. It returns {"accepted": [event_ids], "rejected": [{"event_id": ..., "errors": [...]}]}. Any field outside its allowed values (an unknown event type, unit, trade, currency or country code, a missing evidence link) is rejected with a named error.

Search. search_events(query, since, filters) returns a list of signals in the 2.8 format. Filters: event_type, affected_drivers, affected_countries.

Updated events (D63). A signal with the same event_id and a higher version replaces the previous one; an equal or lower version is rejected as stale. The router re-routes the new version. Auto-built runs from the old version are re-run, and each old run is marked superseded (superseded_by, 4.7). Pending adjustment proposals from the old version are withdrawn and re-proposed. Approved adjustments stay in force but carry review_flag = true in the approval queue.

Flood control (D64, ASSUMPTION). At most one auto-built run per event version, and at most 30 auto-built runs per day in total. Events beyond the cap queue for the next day, and the queue is shown on the dashboard. Scheduled runs (4.10) do not count against the cap.


## 11. Dashboard requirements

The dashboard has nine views over run records, market data, alerts, adjustments and news; it never computes results itself and reaches the engine only through the functions in 4.8 and the human-only functions. The requirements below are independent of the front-end choice (Streamlit, D56).


### 11.1 Rules for every view

Provenance badge on every number: DISCLOSED, DERIVED, MARKET, PROXY or ASSUMPTION (2.4), with the source or formula on hover.

Run flags beside every result: synthetic register (always), proxy-based, uses unconfirmed retrieved data, includes unapproved adjustments, incomplete data.

Every result links to its run record, and any run can be replayed from the view.

Thresholds are labelled PROPOSED ILLUSTRATIVE SETTINGS wherever they are shown.

Human-only actions (approve, reject, revoke, confirm data, record decisions) require a named user and are written to the audit log.

Display currency toggles between USD and EUR for display only (D47).

Nothing executes: no view offers a trade, hedge or payment action.


### 11.2 Views


| View | Contents |
| :--- | :--- |
| Overview | Baseline and active-scenario liquidity paths with the USD 750m reserve line and tier bands; minimum headroom and date; status, OPPORTUNITY label and flags; desk exceptions; top open alerts; adjustments in force; next weekly brief; baseline change since the previous scheduled run, compared with prices held at that run's levels, by driver (4.10); the anchor adjustment beside the baseline, "baseline includes USD X m of market moves since H1 2026", by driver (3.7) |
| Market monitor | One panel per driver with the metrics listed in 6.8, 7.8, 8.8 and 9B.7; volatility regimes; Tier A alerts on the charts; badges for unconfirmed values; the "base rate from H1 2026" freight flag |
| Scenario lab | Preset picker (joint, module and sensitivity family); sliders for every allowed field and override, with range checks; custom scenarios; run; state comparison through run_pair; the sensitivity family side by side |
| Hedge tools | The bunker, rates and FX hedge tools (5.3, 7.7, 8.7, 9B.4): choose target, instrument, coverage and tenor; see saving, cost, settlement and margin cash, and the margin-eligible test |
| Results | Attribution waterfall (full horizon and to the minimum-headroom date); G_d per driver; cash line explorer filtered by driver, line code, currency, date and label; gross and net bunker side by side; rates debt and cash sides; net floating position by month; refinancing watch list |
| FX panel | Net exposure per currency over 13 weeks (9A.4); hedge ratio per currency; translation memo; sign check status |
| Alerts | Tier A, Tier B and desk exception lists with explanation objects (4.7); acknowledge; open, acknowledged and cleared states |
| Approvals | Adjustment approval queue (10.3) with evidence and verifier result; active adjustments panel showing what is changed, why and when it expires, with revoke and the review flag set by an event update (10.6); data confirm queue (3.6); recommendation queue (10.4) |
| News and brief | News events feed with routes and links to the runs they triggered; weekly brief archive with export; the queue of auto-builds waiting for the daily cap (10.6) |


A chat panel is available on every view (10.2). A notes and assumptions page lists the not-modelled notes from each module (6.11, 7.11, 8.11, 9B.10), the provenance legend, every ASSUMPTION parameter with its default and range, the licence lines (3.6) and the decision log.


### 11.3 Demo flow

The demo MUST NOT depend on live news or on a particular outcome from the real profile (D53).

Seeded signals. demo/signals.json holds a sample Red Sea re-escalation signal and a sample regulation signal restricting HSFO use with scrubbers, injected through submit_signal.

Demo mode. A switch that runs the engine on the joint fixture profile (14.3) instead of the generated profile, labelled "demo mode" on every view.

The flow MUST run end to end on the generated data: open the overview at baseline; inject the Red Sea signal; see the router's routes, the auto-built scenario and its run; inspect the freight cash timing and the attribution; test a bunker swap with the hedge tool; read the recommendation and accept or reject it; inject the regulation signal and approve the resulting scrubber-share adjustment; see it in force; generate the weekly brief. The specific outcome "desk exception while the net status stays LOW" is guaranteed only in demo mode on the joint fixture.


## 12. Configuration defaults

All defaults live in five files, loaded and validated at start-up: company_profile.yaml (facts, assumptions, catalogue), thresholds.yaml (Tier A rules, Tier B levels), presets.json, registers/*.yaml and agents.yaml. This table consolidates the defaults set in sections 3–10; the section column is the authority for each.


| Area | Key | Default | Label | Section |
| :--- | :--- | :--- | :--- | :--- |
| Liquidity | minimum_operational_reserve_usd | 750,000,000 (about 15 days of H1 2026 transport expenses) | ASSUMPTION | 3.7 |
| Liquidity | baseline_ocf_daily_usd | H1 2026 OCF ÷ 181 ≈ 4.69m | DERIVED | 3.7 |
| Liquidity | lease_payments_monthly; capex_schedule | Annual lease repayments ÷ 12; guidance or H1 run-rate, monthly (both negative) | DERIVED | 3.7 |
| Liquidity | interest_in_ocf | false | ASSUMPTION | 2.2 |
| Liquidity | count_liquid_investments | false | ASSUMPTION | 3.7 |
| Liquidity | margin_eligible_share_of_T1 | 25% | ASSUMPTION | 3.7 |
| Liquidity | Availability steps | 25% / 50% / 100% reduction | ASSUMPTION | 3.7 |
| Status | Tier B levels | WATCH 10%, HIGH 25%, CRITICAL 50% or any breach | PROPOSED ILLUSTRATIVE | 4.6 |
| Status | Desk exception; OPPORTUNITY | G_d ≥ 25% of H_b*; c ≤ −10% | PROPOSED ILLUSTRATIVE | 4.6 |
| Data | Staleness | Daily series 5 days; weekly series 10 days | ASSUMPTION | 3.6 |
| Data | Plausibility bands | Fuel ±40%, WCI ±80% week on week | ASSUMPTION | 3.6 |
| Data | Volatility regime | Elevated 1.25×, stressed 2.0× | ASSUMPTION | 4.5 |
| Adjustments | max_review_days | 180 | ASSUMPTION | 3.3 |
| Freight | Contract; index-linked share | 50% (swept 30–70%); 10% | ASSUMPTION | 6.3 |
| Freight | β; pricing lag | 1.0; 28 days | ASSUMPTION | 6.5 |
| Freight | κ; clause lag; γ | 0.8; 30 days; 0.5 | ASSUMPTION | 6.5 |
| Freight | Leakage triggers; shares; volume loss | 30% / 30%; 10% / 10% (swept 0–20%); 0% | ASSUMPTION | 6.5 |
| Freight | Non-USD invoicing | 5% EUR (Asia–Europe, Europe–America, Africa & Intraregional); 3% CNY (Asia–America) | ASSUMPTION | 6.3 |
| Freight | Volume growth g; collection lag | 2%; DSO ≈ 49 days | ASSUMPTION; DERIVED | 6.3 |
| Freight | delay_affected_share | 100% | ASSUMPTION | 6.5 |
| Bunker | Hub weights; scrubber share | Singapore 60%, Rotterdam 40%; 15% | ASSUMPTION | 7.3 |
| Bunker | Supplier lag; MFR share and lag | 30 days; 70%, 2 months | ASSUMPTION | 7.3 |
| Bunker | Asia–Europe consumption share; β fallback; margin factor | 35%; 1.0; 1.0 | ASSUMPTION | 7.3–7.7 |
| Rates | Floating debt split; spreads | USD 70% / EUR 30%; SOFR + 200 bp, €STR + 225 bp | ASSUMPTION | 8.3 |
| Rates | Fixed debt rate | 4.8%: the disclosed group weighted average, used as the fixed rate | ASSUMPTION | 8.3 |
| Rates | Interest-bearing cash; currency split; deposit spread | 85%; USD 80% / EUR 20%; benchmark − 10 bp | ASSUMPTION | 8.3 |
| Rates | New-issue premium over BB OAS; swap spread | 0 bp; 0 bp | ASSUMPTION | 8.5, 8.7 |
| Rates | refinancing_mode | rollover | ASSUMPTION | 8.5 |
| Rates | ZIM tranche | USD 4.2bn, SOFR + 225 bp, drawn 1 Jan 2027; switch off until status verified; always shown both ways | Amount DISCLOSED; rest ASSUMPTION | 8.3 |
| FX | CAD hedge ratio | 70% (cap 80%) | ASSUMPTION; cap DISCLOSED | 9B.4 |
| FX | EUR debt; INR and BRL hedge ratios | 100%; 100% at the 31 Dec 2025 rate | ASSUMPTION | 9B.4 |
| FX | EUR operating costs | EUR 350m per quarter | ASSUMPTION | 9B.3 |
| FX | EUR dividend | Latest disclosed dividend (to enter); paid 10 May 2027 | DISCLOSED reference; ASSUMPTION estimate and date | 9B.3 |
| FX | inr_brl_obligations_on; forward points | true; zero | ASSUMPTION | 9B.3, 9A.3 |
| Agents | Models | claude-sonnet-5-5 (reasoning), claude-haiku-5-5 (extraction) | ASSUMPTION | 10.1 |
| Agents | Run cap; brief schedule | 20 runs per session; Monday 08:00 Singapore time | ASSUMPTION | 4.8, 10.2 |
| Signals | Confidence thresholds | Scenario from 0.5; adjustment proposal from 0.7 | ASSUMPTION | 2.8 |
| Signals | Auto-build caps | One per event version; 30 per day, the rest queued for the next day | ASSUMPTION | 10.6 |
| Scheduled runs | Standard set | Baseline plus red_sea_reescalation, demand_slump, oil_spike, fed_cutting_cycle, eur_rally, em_stress and the sensitivity family, after each daily data load | ASSUMPTION | 4.10 |
| Liquidity | anchor_snapshot | H1 2026 averages per series (the OCF run-rate's period), replaced together with the run-rate | DERIVED; DISCLOSED where reported (USD/EUR average) | 3.7 |


Preset values are in 6.6, 7.6, 8.6 and 9B.6; Tier A rules in 4.5 and each module's section 9; catalogue entries in 6.10, 7.10, 8.10 and 9B.9.


## 13. Open items

Every open item has a working default, so the build can start now; the items below change values or labels, not the design. Items marked "before acceptance" MUST be settled before the acceptance suite in section 14 is run on the real profile.


### 13.1 Team decisions


| Item | Working default | Section |
| :--- | :--- | :--- |
| ZIM tranche default once the deal status is known | Off, always shown both ways | 8.3 |



### 13.2 Disclosures to enter or verify (before acceptance)


| Item | Working default | Section |
| :--- | :--- | :--- |
| USD/EUR closing rates: which of 1.1756 and 1.1396 is 30 Jun 2026; the 31 Dec 2025 rate | None; must be entered | 3.1, D12 |
| Trade order and labels of the H1 trade rows (IR p. 10); then relabel DISCLOSED | Order as in 6.2 | 6.2 |
| Latest disclosed dividend, and any stated dividend policy | None; must be entered | 9B.3 |
| Capex: guidance or H1 run-rate | H1 run-rate | 3.7 |
| Annual lease repayments; whether they include interest; current lease liabilities inside USD 1,852m | Lease interest separate; current leases = annual repayments | 3.7, 8.3, D51 |
| Whether interest paid sits inside operating cash flow | Outside (interest_in_ocf = false) | 2.2 |
| Newer report (Q3 2026): replace starting liquidity, the OCF run-rate with the anchor snapshot, P0 and maturities | H1 2026 figures | 3.7, 6.1 |
| Whether the disclosed average freight rate is all-in | All-in | 6.1 |
| Same-quarter trade volumes on the 2026 basis for Q3 and Q4 | Group volume × trade share | 6.3 |
| Scrubber fleet figure | 15% | 7.3 |
| Bond coupon date and whether the 0.25 pp step-up applies | 15 Oct; no step-up | 8.3 |
| Whether EUR 1,999.1m floating debt includes leases | Excludes leases | 8.3 |
| INR and BRL obligations: what they are for, whether settled, payment dates | Paid monthly over 12 months; switch on | 9B.3, D34 |
| ZIM financing status and terms | See 13.1 | 8.3 |
| Anchor snapshot: verify the H1 2026 average USD/EUR (1.1667, cited to IR p. 16), and confirm loaded WCI and fuel history covers January–June 2026 | 1.1667; other values averaged from loaded history | 3.7 |



### 13.3 Modelling assumptions to confirm


| Item | Working default | Section |
| :--- | :--- | :--- |
| Freight: γ, λ, leakage triggers, pricing lag, κ, index-linked share, growth g, delay-affected share | 12 | 6.3, 6.5 |
| Bunker: MFR share and lag (also needed for freight's v2 ex-fuel base rate), hub weights, scrubber share, supplier lag | 12 | 7.3 |
| Bunker: Cape routing uplift and Asia–Europe consumption share | +30%; 35% | 7.6 |
| Rates: floating split, spreads, reset frequency | 12 | 8.3 |
| Rates: interest-bearing share, deposit currency split, spread and term | 12 | 8.3 |
| Rates: new-issue premium over the BB index | 0 bp | 8.5 |
| FX: EUR operating costs; actual CAD hedge ratio | EUR 350m per quarter; 70% | 9B.3, 9B.4 |
| FX: netting across entities (centralised treasury) | Accepted | 9A.4 |
| FX: whether EUR debt hedging covers interest | Principal only | 9B.4 |
| FX: ignoring forward points | Accepted | 9A.3 |
| Minimum reserve; margin-eligible share | USD 750m; 25% of T1 | 3.7, 12 |
| Counterparty register: banks and shares | Assumed list of relationship banks | 3.4 |
| Confidence thresholds (0.5, 0.7) and the daily auto-build cap (30) | As stated | 2.8, 10.6 |



### 13.4 Sources and APIs to confirm at build time


| Item | Section |
| :--- | :--- |
| WCI route values publicly reported weekly | 3.6, 6.4 |
| VLSFO and HSFO prices for Singapore and Rotterdam publicly reported weekly | 3.6, 7.4 |
| Whether "MFO 0.5% FOB Rotterdam" corresponds to VLSFO | 7.11 |
| Series IDs and access: NY Fed SOFR, FRED DGS2, BB OAS, DCOILBRENTEU, ECB €STR and 2-year yield curve, ECB FX | 3.6, 8.4 |
| FOMC and ECB meeting dates for the horizon | 8.3 |
| Send the preset ID list (2.7) to the news engine team | 2.7, 2.8 |



## 14. Build order and acceptance tests

The build runs in nine steps, each finished by its own tests, and the prototype is accepted when the suite in 14.3 passes on the fixtures and the joint Red Sea test reproduces its hand-computed figures.


### 14.1 Build order

Contracts (section 2): Pydantic models for cash line, volume line, scenario parameters (with zero_shock() and the shock/state split), hedge test, signal, adjustment, run record, alert, recommendation; the line code, label and event type enums; submit_signal validation (2.8, 10.6).

Profile and registers (3.1–3.5): company_profile.yaml with section 12 values, the catalogue and registers; resolve() with the three layers; the deterministic exposure generator, including the single maturity schedule.

Storage and ingestion (3.6): SQLite tables; ECB, NY Fed and FRED connectors with carry-forward; hand-loaded CSV history for WCI and fuel; the data retrieval agent with the confirm queue.

Modules: freight and bunker together (linked through volume lines), then rates, then the FX module (9B), each with its fixture and worked example.

Engine (section 4, 9A): two-pass orchestrator, snapping, conversion layer with policy hedges, pairing, attribution, liquidity tests, Tier A and Tier B, run record, replay, run_pair, the engine walkthrough test; scheduled runs and the baseline change (4.10); the anchor adjustment (3.7).

Agent tools (4.8), including the run cap and the exclusion of human-only functions.

Agents (section 10): router, adjustment, scenario builder, verifier, recommendation, alert investigator, chat, weekly brief.

Dashboard (section 11).

Acceptance suite and demo (14.3, 11.3).


### 14.2 Suggested plan for five people over four weeks


| Week | Engine and contracts | Freight and bunker | Rates and FX | Agents and news interface | Dashboard |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Contracts, profile, resolve(), generator | Fixtures, CSV history, WCI and fuel retrieval | Connectors (ECB, NY Fed, FRED), fixtures | Signal schema implemented as agreed (2.8); agent tool stubs; seeded demo signals | Layout, provenance badges, run viewer |
| 2 | Orchestrator, snapping, pairing, walkthrough test | Freight and bunker modules, worked examples | Rates module and 9B, worked examples | Router, adjustment agent, propose_adjustment | Market monitor, scenario lab |
| 3 | 9A conversion, attribution, liquidity, alerts, replay | Hedge tool (bunker), back-tests | Swap tool, FX policy hedges, sign and static checks | Scenario builder, verifier, recommendation, alert investigator; milestone: first real news signals flowing | Results, FX panel, alerts, approvals |
| 4 | Joint Red Sea test, acceptance suite | Bug fixing, calibration chart | Calibration check | Chat, weekly brief | News and brief, demo flow |



### 14.3 Acceptance tests

Engine

A zero-shock run gives delta_usd == 0 on every pair and identical paths.

The engine walkthrough (4.9) reproduces every figure.

Attribution bars sum to the total within USD 0.01, over the full horizon and to the minimum-headroom date.

Gross lines stay un-netted until aggregation; netting changes no USD total.

Every non-USD line carries both FX rates and both converted amounts; none is defaulted.

replay(run_id) reproduces a run from its record alone, including after a retrieved value is corrected.

Overrides change results without creating a profile version; run_pair shows state effects and never as deltas.

Status precedence: a stale material series turns LOW into INCOMPLETE but leaves CRITICAL as CRITICAL with the "incomplete data" flag; the proxy-based flag raises LOW to WATCH.

A baseline breach is CRITICAL regardless of the scenario.

Modules: every module acceptance test in 6.15, 7.15, 8.15 and 9B.14 passes, including each worked example on its own scenario.

Joint Red Sea test (hand-computed). Fixture tests/fixtures/joint_red_sea.yaml combines the freight and bunker fixtures (6.14, 7.14) with a 5% EUR invoicing share on freight at a T0 rate of 1.15 USD per EUR (unchanged in the scenario). Baseline: T1 = 1,500, T2 = 200, reserve 750, OCF run-rate and leases zero, one capex payment of −450 on 30 Sep 2027, so H_b = 950 until then and H_b* = 500 on 30 Sep 2027. Scenario: the Red Sea preset's freight and bunker parts (USD m).


| Check | Expected |
| :--- | :--- |
| TEU transfer | Bunker's MFR uses freight's scenario volume line: 300,000 TEU for November 2026 |
| FX conversion | Freight's EUR lines carry rates 1.15 in both passes; FX bar 0.00; USD totals equal those of 6.14 |
| Cumulative net delta, 30 Dec 2026 | −213.692 (bunker −59.692, freight −154.00) |
| Cumulative net delta, 31 Dec 2026 (lowest point) | −227.692 |
| Cumulative net delta, 30 Jan 2027 and after | −37.559 (freight +15.96, bunker −53.519) |
| Attribution | Freight +15.96; bunker −53.519; rates 0.00; FX 0.00; total −37.559 |
| Scenario headroom | 722.308 on 31 Dec 2026; H_s* = 462.441 on 30 Sep 2027 |
| Headroom consumed | c = 37.559 ÷ 500 = 7.51%: status LOW |
| Desk exception | G_freight = 168.00 = 33.6% of H_b*, at or above 25%: fires. G_bunker = 59.692 (11.9%): does not |
| Reserve breach variant | With capex −920: H_b* = 30, H_s* = −7.559 on 30 Sep 2027: breach, status CRITICAL |


The test shows the point of desk exceptions: a temporary gross hit of USD 168m from slower collections, hidden in the net status because it recovers before the baseline's own low point.

Data

A stale material series makes a run INCOMPLETE; a value outside its plausibility band goes to the confirm queue and raises "possible major move, pending confirmation".

Units: USD/FEU halved, USD/bbl rejected, TEU and TTEU never mixed; every FX value stored as USD per unit.

Licence lines appear on every view that shows retrieved data.

Adjustments and agents

Every catalogue event_types value exists in the 2.8 enum; signals with unknown types are rejected.

Each value mode (step_only, step_or_direct, switch) accepts and rejects the right proposals; a future-dated sizing adjustment raises a warning.

An approved adjustment applies from its effective date to its review date; expired ones drop out; the run record lists them.

A bank-failure signal on a register bank yields an availability proposal for that bank's tiers only; an unknown bank yields "relevant, exposure unknown" and no baseline proposal.

Agents cannot call approve, reject, revoke or confirm functions; the 20-run cap is enforced.

Unapproved adjustments appear only in auto-built runs, which carry the flag.

Every number in an agent output traces to a run ID, metric or event ID.

The weekly brief contains the three sections of 10.5 with sources.

A signal whose summary contains an instruction (for example to approve an adjustment) produces no approval and no out-of-catalogue proposal, and the instruction is logged.

News engine interface and scheduled runs

submit_signal rejects unknown event types, units, trades, currency and country codes and preset IDs, each with a named error; a valid batch returns its event IDs as accepted.

A version update re-routes the event, re-runs its auto-built run and marks the old run superseded, withdraws pending proposals from the old version and flags approved ones for review; an equal or lower version is rejected.

Confidence routing: at 0.49 the signal goes to recommendation only; at 0.5 and 0.69 it may build a scenario but never an adjustment; at 0.7 it may also propose an adjustment.

Magnitude mapping: SOFR and ESTR in bp set a rate_path step for USD and EUR on published_at; VLSFO and HSFO in pct set their shocks; a WCI route or trade in pct sets that trade's rate_shock_pct; a currency in pct sets its move_pct; an unmatched subject or unit is treated as qualitative; a mapped value outside its 2.7 range is rejected.

expected_duration_days sets the auto-built scenario's duration_days; null uses the preset's.

The 31st auto-build of a day is queued for the next day and shown in the queue; a second auto-build for the same event version is refused.

The run summary validates against its schema, and the joint Red Sea run returns the example in 4.8.

search_events returns signals in the 2.8 format, filtered by event_type, affected_drivers and affected_countries.

After a daily data load, scheduled runs exist for the baseline, the six standard presets and every sensitivity case, with source = scheduled.

The baseline-change parts (other plus four drivers) sum exactly to the total change; a fuel-price-only move between snapshots puts its whole market effect in the bunker part.

Anchor adjustment

With T0 prices equal to the anchor snapshot, the anchor adjustment is exactly zero on every date.

The four driver parts of the anchor adjustment sum exactly to the total, both as cash by date and as the change in minimum headroom.

The anchor level has no effect on the weekly driver effects: moving the anchor snapshot changes every step's anchored baseline by the same cash amounts, so each driver's change in the cash path is unchanged, and on a fixture whose minimum-headroom date does not move, so is its change in minimum headroom. A price move between last week's and this week's snapshots does change the driver effects.

BASE_AMOUNT lines contribute nothing to the anchor adjustment.

Entering a newer report replaces the OCF run-rate and the anchor snapshot together, in one profile version.

Dashboard: every number shows a provenance badge; every result shows its flags and run link; human-only actions record a named user; the demo flow in 11.3 runs end to end; nothing offers execution.


### 14.4 Minimum viable scope and stretch

If week 4 runs short, stretch items are dropped first; the must-have set alone still delivers every item in the project brief and the demo.


| Priority | Engine and modules | Agents | Dashboard and other |
| :--- | :--- | :--- | :--- |
| Must have | Engine, all four modules, Tier A and Tier B, bunker hedge tool | Data retrieval, signal router, scenario builder, adjustment, verifier, recommendation, chat, weekly brief | Overview, market monitor, scenario lab, results, alerts and approvals views; a simple brief view that renders the latest weekly brief (part of the News and brief view); joint test; demo script and demo mode |
| Stretch | Rates swap tool, FX hedge-ratio tool, replay after a data correction | Alert investigator (the scenario builder covers severe alerts) | FX panel; the news feed and brief archive; calibration charts; PDF export of the brief; full alert lifecycle |


Tests that cover stretch items are marked optional and skipped if the item is dropped: the correction case in engine test 6, the swap tool test (8.15, item 8), the FX hedge-ratio tool cases, and alert lifecycle checks.


## 15. Guide for adding future workflows

A new driver is added as one more module under the template in section 5; the engine, conversion layer, liquidity path, alerts and agents need no redesign, only registrations. The test of a good candidate is the module's one question: what does this driver do to cash, line by line?


### 15.1 Steps

Write the question and boundary (template heading 1): what the module owns, what stays with existing modules, and how double counting is prevented. Add a decision log row for any boundary change.

Register line codes in the 2.2 enum, each with an owner and a baseline treatment (BASE_AMOUNT or DELTA_ONLY), and declare the module's gross and offset codes.

Add profile entries with labels, sources, formulas, allowed ranges and rationales; mark each parameter as sizing or time-varying.

Write the exposure recipe as a deterministic generator step; give every item one owner and a sub_id.

Add market series to the 3.6 catalogue with source, frequency, unit, label and material flag; reuse the shared connectors or the retrieval agent pattern.

Extend ScenarioParams with a new block; classify every field as shock or state, extend zero_shock(), and add range checks.

Add presets to presets.json with scope and rationale, and extend joint presets where the event affects the new driver.

Add Tier A rules to thresholds.yaml and a playbook.

Add catalogue entries for agent-adjustable parameters, using only event types in the 2.8 enum; extend the enum first if needed, with the catalogue test.

Add reference register entries the adjustment agent needs to judge relevance.

Write notes for anything not modelled, with disclosed sizes.

Build a fixture and a hand-computed worked example, and the module acceptance tests; extend the joint test if the module interacts with others (as freight's TEU feeds bunker).

Add a dashboard panel with the module's metrics and, if relevant, a hedge tool.

Update the agents: tell the signal router which event types and registers now map to the module; the brief picks it up through run records.


### 15.2 Roadmap candidates

These are roadmap candidates after the four-driver prototype. Priority indicates recommended sequencing, not inclusion in v1. Promote an item into scope only after its exposure owner, data source and validation approach are agreed. Agent findings and proposed changes remain subject to human review.


| Priority | Effort | Workflow | Initial implementation slice | Starting data |
| :--- | :--- | :--- | :--- | :--- |
| High | Low–Medium | Collections and working capital | Add DSO timing shocks by trade or country. Decide whether freight or a shared working-capital module owns the logic; handle receivables outstanding at T0 explicitly. | Disclosed receivables and revenue; payment rules and news |
| High | Medium | Sanctions and restricted-party screening | Screen named parties against public lists and link possible matches to affected exposures for human review. Start with flags; no automatic cash restrictions. | US OFAC and EU consolidated lists; counterparty, supplier and trade registers |
| High | Low | CO2 / EU ETS | Add a EUR certificate-cost cash line with phase-in assumptions and allowance-price retrieval. | Disclosed certificate cost; EUA prices |
| Medium–High | Medium | Macro demand signals | Use macro indicators to inform freight-volume assumptions and presets; require approval before changing inputs. | Free macro series such as FRED trade and activity data |
| Medium | Medium | Non-fuel operating-cost inflation | Add a cash-line schedule for port, terminal-handling and charter costs; define exposure and scenario links first. | Disclosed costs; public charter indices where available; inflation series |
| Medium | Low–Medium | Trapped cash and capital controls | Map availability reductions and payment delays by currency and country to affected cash lines. | News; operating-footprint register |
| Medium | Low | Disruption preset family | Map strikes, port outages and canal closures to documented delay and exposure assumptions in reusable presets. | News event types |
| Low | Low | Counterparty and bank risk | Use bank stress signals to propose availability changes for deposits, credit lines and hedge exposure, with review. | Counterparty register; ratings or bank equity prices |
| Later | To size | Demurrage and detention | First define volume and dwell-time assumptions for revenue not assigned to trades. | Disclosed revenue not assigned to trades |

