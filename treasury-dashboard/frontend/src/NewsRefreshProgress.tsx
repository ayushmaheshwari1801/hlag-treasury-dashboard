import { useEffect, useState } from "react";

const stages: Record<string, string> = {
  search: "Searching for news", classification: "Reviewing articles",
  consolidation: "Grouping related news", association: "Checking related developments",
  analysis: "Assessing treasury relevance", processing: "Processing news",
};
export default function NewsRefreshProgress({ stage, status }: { stage: "collecting" | "updating"; status?: any }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const started = status?.started_at ? Date.parse(status.started_at) : NaN;
  const elapsed = Number.isFinite(started) ? Math.max(0, Math.floor((now - started) / 1000)) : null;
  const label = stage === "updating" ? "Updating dashboard" : stages[status?.stage] ?? "Collecting and analysing news";
  const total = status?.running && status.total > 0 ? status.total : undefined;
  return <div className="news-refresh-progress" role="status" aria-live="polite">
    <div className="news-refresh-caption"><span>{label}…</span><span aria-live="off">{elapsed !== null ? `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")} elapsed` : "In progress"}</span></div>
    <progress aria-label={label} max={total} value={total ? status.completed : undefined} />
    {total && <p className="small muted">{status.completed} / {total} in this stage</p>}
    {status?.database_totals && <p className="small muted">Stored so far: {status.database_totals.articles} Articles · {status.database_totals.events} Events</p>}
  </div>;
}
