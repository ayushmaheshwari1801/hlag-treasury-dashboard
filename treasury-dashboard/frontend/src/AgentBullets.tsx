import { treasuryText } from "./treasuryCopy";

/** Format both historical prose and new bullet responses without dropping facts or caveats. */
export function agentPoints(value: string | string[] | null | undefined): string[] {
  return (Array.isArray(value) ? value : [value ?? '']).flatMap(text =>
    treasuryText(text).split(/\n+/)
      .map(line => line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, '').replace(/\*\*/g, '').trim())
      .flatMap(line => line.split(/(?<=[.!?])\s+(?=[A-Z])/).reduce<string[]>((parts, part) => {
        if (parts.length && /(?:\b[A-Z]\.|\b(?:Mr|Ms|Dr|Inc|Ltd)\.)$/.test(parts[parts.length - 1])) parts[parts.length - 1] += " " + part;
        else parts.push(part);
        return parts;
      }, []).flatMap(sentence => sentence.split(/;\s+/)))
      .filter(Boolean));
}

export function AgentBullets({ text, className = '' }: {text: string | string[] | null | undefined; className?: string}) {
  const points = agentPoints(text);
  return <ul className={`agent-bullets ${className}`}>{(points.length ? points : [
    'No assessment is available from the current information.',
    'The cash amount, timing and recommended response have not been established.',
    'Review the supporting evidence and reassess when the missing information is available.',
  ]).map((point, i) => <li key={i}>{point.split(/\s+/).length > 45 ? <details><summary>Supporting assessment — expand for context</summary><p>{point}</p></details> : point}</li>)}</ul>;
}
