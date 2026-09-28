'use client';

/**
 * Header pill showing whether the backend and its dependencies are actually up.
 *
 * This exists so a broken environment is visible *before* the user picks files and
 * watches an upload fail: if ffprobe is missing every upload will be rejected, and if
 * the queue is down processing cannot start. The check runs every 20s — frequent
 * enough to notice a restart, rare enough to be free.
 */

import { useEffect, useState } from 'react';
import { health, type Health } from '@/lib/api';
import { Badge } from './Badge';

const POLL_MS = 20_000;

export function BackendStatus() {
  const [state, setState] = useState<{ health: Health | null; reachable: boolean; checked: boolean }>({
    health: null,
    reachable: false,
    checked: false,
  });

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const result = await health();
        if (!cancelled) setState({ health: result, reachable: true, checked: true });
      } catch {
        if (!cancelled) setState({ health: null, reachable: false, checked: true });
      }
    };
    check();
    const timer = setInterval(check, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (!state.checked) return <Badge>checking…</Badge>;
  if (!state.reachable) return <Badge tone="bad">API offline</Badge>;

  const h = state.health!;
  const down = Object.entries({
    database: h.database,
    queue: h.queue,
    storage: h.storage,
    ffprobe: h.ffprobe,
  })
    .filter(([, ok]) => !ok)
    .map(([name]) => name);

  if (down.length === 0) return <Badge tone="ok">API healthy</Badge>;
  return <Badge tone="warn" title={down.map((d) => `${d}: ${h.detail[d] ?? 'unavailable'}`).join(' · ')}>
    {down.join(', ')} down
  </Badge>;
}
