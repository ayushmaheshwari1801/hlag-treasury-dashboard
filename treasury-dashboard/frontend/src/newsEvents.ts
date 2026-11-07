/** One tracker entry per v8 Event; article IDs remain available for original evidence. */
export function groupNewsEvents(items: any[]) {
  const groups = new Map<string, any>();
  for (const item of items) {
    const key = item.event_id ?? item.id;
    let event = groups.get(key);
    if (!event) {
      event = { ...item, id: key, title: item.event_title ?? item.event_labels?.[0] ?? item.title,
        score: typeof item.score === "number" ? item.score : item.score?.total ?? null,
        article_ids: [], articles: [], modules: [] };
      groups.set(key, event);
    }
    for (const id of item.article_ids?.length ? item.article_ids : [item.id]) {
      if (!event.article_ids.includes(id)) event.article_ids.push(id);
    }
    if (!event.articles.some((a: any) => a.id === item.id)) event.articles.push(item);
    event.modules = [...new Set([...event.modules, ...(item.modules ?? [])])];
    if ((item.published_at ?? "") > (event.published_at ?? "")) event.published_at = item.published_at;
  }
  return [...groups.values()].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
}
