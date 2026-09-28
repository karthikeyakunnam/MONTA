'use client';

/**
 * The raw event log, newest last, exactly as the pipeline published it.
 *
 * This is the receipt for the status rail: if the rail says "analyzing" the feed shows
 * which clip and when. It also makes the no-fake-progress rule verifiable by eye — a
 * stage with no percentage in the feed shows no percentage in the UI.
 */

import { useEffect, useRef } from 'react';
import type { ProgressEvent } from '@/lib/api';
import { STAGE_LABELS } from '@/lib/status';

export function ProgressFeed({ events }: { events: ProgressEvent[] }) {
  const endRef = useRef<HTMLLIElement>(null);
  const count = events.length;

  useEffect(() => {
    // Only autoscroll the feed's own container, never the page.
    endRef.current?.scrollIntoView({ block: 'nearest' });
  }, [count]);

  if (events.length === 0) {
    return <div className="empty">No pipeline events yet. Start processing to see live updates here.</div>;
  }

  return (
    <ul className="feed">
      {events.map((event, i) => (
        <li key={`${event.at}-${i}`} ref={i === events.length - 1 ? endRef : undefined}>
          <span className="time">{clockTime(event.at)}</span>
          <span className={`stage ${event.stage}`}>{STAGE_LABELS[event.stage] ?? event.stage}</span>
          <span>
            {event.message}
            {typeof event.percent === 'number' && <span className="muted"> · {event.percent.toFixed(0)}%</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function clockTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '--:--';
  return date.toLocaleTimeString(undefined, { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
