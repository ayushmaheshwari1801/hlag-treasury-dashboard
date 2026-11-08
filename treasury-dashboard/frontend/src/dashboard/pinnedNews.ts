// News Events the treasurer moved into the News Tracker by hand. They are shown first in their category, in addition
// to the automatic top Events, and get their own cash-impact estimate like any other tracker row.
// Kept in this browser only: it is a viewing preference, not a change to the saved news or the scenario.
import { useEffect, useState } from "react";

const KEY = "treasury.pinnedNews.v1";
const FIELDS = ["id", "event_id", "title", "published_at", "source", "category", "score", "article_ids", "modules"] as const;

export type PinnableRow = { id: string; event_id: string; title: string; category?: string; score?: number | null; published_at?: string; article_ids: string[]; modules: string[]; pinned?: boolean };

/** Only the fields the tracker row needs, so stored entries stay small. */
export function pinRecord<T extends PinnableRow>(row: T): PinnableRow {
  return Object.fromEntries(FIELDS.filter(k => (row as any)[k] !== undefined).map(k => [k, (row as any)[k]])) as PinnableRow;
}

/** Pinned rows first (marked pinned), then the automatic rows that are not already pinned. */
export function mergePinned<T extends PinnableRow>(auto: T[], pinned: PinnableRow[]): (T | PinnableRow)[] {
  const ids = new Set(pinned.map(p => p.event_id));
  return [...pinned.map(p => ({ ...(auto.find(a => a.event_id === p.event_id) ?? p), pinned: true })),
          ...auto.filter(a => !ids.has(a.event_id))];
}

/** Drop pins whose Event is no longer in the current news; refresh the stored copy of the rest. */
export function reconcilePinned(pinned: PinnableRow[], current: PinnableRow[]): PinnableRow[] {
  return pinned.flatMap(p => { const now = current.find(c => c.event_id === p.event_id); return now ? [pinRecord(now)] : []; });
}

function load(): PinnableRow[] {
  try { const v = JSON.parse(localStorage.getItem(KEY) ?? "[]"); return Array.isArray(v) ? v.filter(x => x?.event_id && x?.title) : []; }
  catch { return []; }
}

export function usePinnedNews() {
  const [pinned, setPinned] = useState<PinnableRow[]>(load);
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(pinned)); } catch { /* storage unavailable: pins last for this visit */ } }, [pinned]);
  return {
    pinned,
    isPinned: (eventId: string) => pinned.some(p => p.event_id === eventId),
    toggle: (row: PinnableRow) => setPinned(list => list.some(p => p.event_id === row.event_id)
      ? list.filter(p => p.event_id !== row.event_id) : [...list, pinRecord(row)]),
    unpin: (eventId: string) => setPinned(list => list.filter(p => p.event_id !== eventId)),
    reconcile: (current: PinnableRow[]) => setPinned(list => { const next = reconcilePinned(list, current);
      return JSON.stringify(next) === JSON.stringify(list) ? list : next; }),
  };
}
