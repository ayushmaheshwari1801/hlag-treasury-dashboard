import { useEffect, useState } from "react";
import { api } from "./api";
import { Drawer } from "./Drawer";
import { num, ROW_LABEL, when } from "./format";
import { cashDetailRows } from "./cashDetailRows";

/** The row's paired cash lines from the saved run: after T0 and inside the selected horizon, as in the row figure. */
export function CashLinesTable({ runId, rowId, horizonEnd }: { runId: string; rowId: string; horizonEnd?: string }) {
  const [run, setRun] = useState<any>(null), [error, setError] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => { let active = true; setPage(0); api.riskRecord(runId).then(r => { if (active) setRun(r); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [runId]);
  if (error) return <p className="err">{error}</p>;
  if (!run) return <p className="muted">Loading saved calculation…</p>;
  const lines = cashDetailRows(run.lines ?? [], rowId, horizonEnd, run.as_of);
  const total = lines.reduce((sum: number, line: any) => sum + line.attributed_usd_m, 0);
  return <>
    <p className="small muted">{run.as_of} to {horizonEnd ?? run.scenario_path?.[run.scenario_path.length - 1]?.date} · Native and USD amounts in millions</p><p className="foot">Attributed row total: USD {num(total, 3)}m. Native driver changes use baseline FX; the remaining conversion effect belongs to FX.</p>
    {!!run.missing_inputs?.length && <details><summary>Missing or unverified inputs ({run.missing_inputs.length})</summary><ul className="small muted">{run.missing_inputs.map((m: string) => <li key={m}>{m}</li>)}</ul></details>}
    <div className="cash-detail-table"><table><thead><tr><th>Date / cash flow</th><th>Currency</th><th>Native base</th><th>Native scenario</th><th>USD base</th><th>USD scenario</th><th>Full USD delta</th><th>Attributed USD delta</th><th>Baseline treatment</th></tr></thead><tbody>{lines.slice(page * 40, page * 40 + 40).map((l: any, i: number) => <tr key={`${l.sub_id}-${i}`}><td>{l.date}<small>{l.line_code.replace(/_/g, " ")}</small></td><td>{l.currency}</td><td>{num(l.amount_native_base_m, 3)}</td><td>{num(l.amount_native_scenario_m, 3)}</td><td>{num(l.amount_usd_base_m, 3)}</td><td>{num(l.amount_usd_scenario_m, 3)}</td><td>{num(l.delta_usd_m, 3)}</td><td className={l.attributed_usd_m < 0 ? "neg" : "pos"}>{num(l.attributed_usd_m, 3)}</td><td>{l.treatment === "DELTA_ONLY" ? "Only the change affects liquidity" : l.treatment === "BASE_AMOUNT" ? "Included in baseline" : "Not specified"}</td></tr>)}</tbody></table></div>
    {!lines.length && <p className="muted small">No cash lines match this driver or currency.</p>}<div className="cash-detail-pages"><button className="preset" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous</button><span className="small muted">{lines.length ? page * 40 + 1 : 0}–{Math.min(lines.length, (page + 1) * 40)} of {lines.length}</span><button className="preset" disabled={(page + 1) * 40 >= lines.length} onClick={() => setPage(p => p + 1)}>Next</button></div>
    <details><summary>Input market snapshot</summary><div className="cash-detail-table"><table><thead><tr><th>Series</th><th>Value</th><th>Date</th><th>Source</th></tr></thead><tbody>{Object.entries(run.market_snapshot ?? {}).map(([key, value]: [string, any]) => <tr key={key}><td>{key.replace("FX_USD_PER_", "USD per ").replace(/_/g, " ")}</td><td>{num(value.value, 4)}</td><td>{value.date}</td><td>{value.source}</td></tr>)}</tbody></table></div></details>
  </>;
}

/** Review of the saved calculation (POST/GET /api/v1/risk/runs/{id}/reviews). */
export function ReviewSection({ runId }: { runId: string }) {
  const [reviewer, setReviewer] = useState(""), [comment, setComment] = useState(""), [reviewMessage, setReviewMessage] = useState("");
  const [reviews, setReviews] = useState<any[]>([]), [reviewBusy, setReviewBusy] = useState(false);
  useEffect(() => { let active = true; setReviewMessage(""); setReviews([]); api.runReviews(runId).then(r => { if (active) setReviews(r.reviews); }).catch(e => { if (active) setReviewMessage(e.message); }); return () => { active = false; }; }, [runId]);
  const recordReview = async () => {
    setReviewBusy(true);
    try { await api.reviewRun(runId, reviewer.trim(), comment); setReviewMessage("Review saved for this calculation."); setReviews((await api.runReviews(runId)).reviews); setComment(""); }
    catch (e: any) { setReviewMessage(e.message); } finally { setReviewBusy(false); }
  };
  return <section><h3>Review saved calculation</h3><p className="small muted">Saves your review with the figures and assumptions shown. No trade or payment is executed.</p>
    <input className="inp" aria-label="Reviewer name" placeholder="Reviewer name" value={reviewer} onChange={e => setReviewer(e.target.value)} />
    <textarea className="inp" aria-label="Review comment" placeholder="Comment" value={comment} onChange={e => setComment(e.target.value)} />
    <button className="primary" disabled={!reviewer.trim() || reviewBusy} onClick={recordReview}>Record review</button>
    {reviewMessage && <p role="status">{reviewMessage}</p>}
    {!!reviews.length && <details><summary>Previous reviews</summary><ul>{reviews.map(r => <li key={r.id}>{r.reviewer} · {when(r.at)} · {r.comment}</li>)}</ul></details>}
  </section>;
}

/** Fallback for runs without the Methodology view (non-engine runs): the row's cash lines and the review. */
export default function CashDetails({ runId, rowId, rowLabel, horizonEnd, onClose }: { runId: string; rowId: string; rowLabel?: string; horizonEnd?: string; onClose: () => void }) {
  return <Drawer title={`${rowLabel ?? ROW_LABEL[rowId] ?? rowId} · cash-flow detail`} onClose={onClose} modal wide>
    <CashLinesTable runId={runId} rowId={rowId} horizonEnd={horizonEnd} />
    <ReviewSection runId={runId} />
  </Drawer>;
}
