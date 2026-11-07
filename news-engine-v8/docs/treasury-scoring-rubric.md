# Treasury R5 scoring ? contract 8

Strict integer P/R/U from 0 to 5; total = 8P + 7R + 5U. Each dimension requires a nonempty explanation. R5_CAPS_ENABLED remains False; anchors are guidance and the dimensions are independent. Re-enabling the flag restores applicable basis, cross-dimensional and action-constraint caps; Claim Type caps were removed with that model.

Analysis reads all assigned original Articles, dates and Treasury context. Numerical source checks are disabled (NUMERIC_GROUNDING_ENABLED=False); source-faithful prose is the model's responsibility. Python still validates R5 integer ranges, arithmetic and explanation structure.

## Rubric supplied to W4

```text
锘縏arget: Hapag-Lloyd, global container carrier with terminal activities. Treasury
channels are funding, FX, liquidity, settlement, compliance and cash planning.
Oil/bunker benchmarks directly affect cost/cash forecast assumptions. USD FX,
relevant currencies, policy rates and funding conditions are direct monitoring
channels even without a company mention. Operating shipping/port/freight changes
need an explicit cost/cash, collection/payment, financing, settlement or compliance
bridge. Physical disruption is not a financial-channel blockage.
Use the fixed reference_time and explain timing uncertainty.
Choose supported issue-level P/R/U anchors from the assigned original Articles.
Do not mix unrelated issues or retain historical score maxima. Account for evidence gaps when choosing anchors. Publication,
headline, prestige, geography and company mentions do not independently add points.

P (impact):
0 no usable material development or credible Treasury connection.
1 unchanged policy/benchmark, broad commentary or forecast without a changed direct
  Treasury driver/concrete obligation.
2 observed fuel/FX/rate/funding driver changes or operating changes affect cost/cash
  forecast assumptions, without specific financial obligation/access/control change.
3 concrete new financial action, rule, settlement condition or public cash commitment
  changes funding/hedging/payment/compliance planning or controls.
4 binding funding/settlement/convertibility/accessible-cash channel constraint, with
  alternatives or no proof of complete loss of the capability.
5 actual loss of relevant payment/funding/cash capability, or an imminent binding
  financial obligation impossible to meet through available permitted channels.
Geography, price size, carriers affected and closures are contextual evidence;
explain the financial channel. Do not invent a liquidity failure.

R (relevance):
0 no credible Treasury channel or useful related background.
1 adjacent maritime topic with only a speculative financial connection.
2 indirect GDP/aggregate trade/sector background with no direct benchmark or
  demonstrated business-to-cash/financial mechanism.
3 concrete operating-to-cash/cost bridge, or financial-rule applicability dependent
  on an unproven country/contract/bank relationship.
4 established direct fuel/oil, USD FX, relevant currency/rate/funding benchmark or
  business-class financial/control requirement needing no unknown local/position
  assumption. A company mention is unnecessary for these channels.
5 company (or evidenced instrument/transaction/counterparty) explicitly party to a
  specific financing/settlement/cash-flow/FX/liquidity/compliance fact. An operating
  route or company name alone is insufficient.
Never infer net exposure from a public mention.

U (urgency of a Treasury response):
0 no current need or expired deadline with no ongoing consequences.
1 background/long horizon or concrete Treasury action >90 calendar days away.
2 routine benchmark/forecast monitoring, unbound proposal or action in 31-90 days.
3 concrete Treasury action in 8-30 days, or changed financial/settlement/compliance
  conditions already operative with ongoing response but no proven near deadline.
4 relevant financial/payment/control action due in 3-7 days with sourced operative
  date and applicable response.
5 such an action due in 0-2 days or Treasury payment/funding/cash access now blocked.
Explain sourced response timing, any concrete Treasury obligation/access/control
change and evidence gaps. Do not invent deadlines or blocked financial access.

Calibration: policy hold=(1,4,2); changed USD rate/FX or observed oil benchmark with
no specific obligation=(2,4,2); port delay with cash forecast bridge and unknown
company exposure=(2,3,2); named company financing commitment tomorrow with no loss
of access=(3,5,5); applicable global payment-control implementation in five days
without access loss=(3,4,4); relevant payment channel constrained with alternatives
now=(4,4,5); public proof of company payment capability blocked without permitted
alternative=(5,5,5). These are illustrative, not facts about current exposure.
Reasons: one or two concrete short sentences per dimension about this source-backed
change, its Treasury channel and timing/limits; no copied anchors or generic filler.

```
