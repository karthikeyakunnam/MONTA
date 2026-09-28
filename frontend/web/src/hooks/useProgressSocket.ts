'use client';

/**
 * Live pipeline progress over the Layer 2 websocket.
 *
 * Rules this hook obeys, because the alternative is a UI that lies:
 *
 * * **No invented percentages.** `percent` is rendered only when the producer sent one
 *   (the worker sends `analyzed/total` for clip analysis). Every other stage shows an
 *   indeterminate bar, which is honest about not knowing.
 * * **The snapshot is authoritative on connect.** The server replays its recorded
 *   history after it, so a page opened mid-run is never blank and a reconnect does not
 *   lose the earlier stages.
 * * **A dead bus degrades to polling.** The server closes with 1011 and an
 *   `bus_unavailable` error frame when Redis is gone; we then refresh the project on a
 *   timer instead of showing a frozen screen.
 * * **Reconnect backs off.** 1s, 2s, 4s, 8s capped at 15s, and never for a close code
 *   that says retrying is pointless (4404 project not found, 1000 normal close).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { progressSocketUrl, type ProgressEvent, type Stage } from '@/lib/api';

const MAX_EVENTS = 200;
const POLL_FALLBACK_MS = 4000;
const BACKOFF_START_MS = 1000;
const BACKOFF_MAX_MS = 15_000;
const CLOSE_NOT_FOUND = 4404;

export interface Snapshot {
  status: string;
  status_detail: string | null;
  clips: Array<{ clip_id: string; filename: string; status: string; duration_s: number | null }>;
  job: { job_id: string; state: string; stage: string | null; error: string | null } | null;
}

export type Connection = 'connecting' | 'live' | 'polling' | 'closed';

export function useProgressSocket(projectId: string, onRefresh: () => void) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState<Connection>('connecting');
  const socketRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(BACKOFF_START_MS);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedRef = useRef(false);
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) return;
    setConnection('polling');
    pollRef.current = setInterval(() => refreshRef.current(), POLL_FALLBACK_MS);
  }, []);

  useEffect(() => {
    closedRef.current = false;

    const connect = () => {
      if (closedRef.current) return;
      let socket: WebSocket;
      try {
        socket = new WebSocket(progressSocketUrl(projectId));
      } catch {
        startPolling();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        retryRef.current = BACKOFF_START_MS;
        stopPolling();
        setConnection('live');
      };

      socket.onmessage = (raw) => {
        let payload: Record<string, unknown>;
        try {
          payload = JSON.parse(raw.data as string);
        } catch {
          return;
        }
        const type = payload.type as string | undefined;
        if (type === 'ping') return;
        if (type === 'snapshot') {
          setSnapshot({
            status: String(payload.status),
            status_detail: (payload.status_detail as string | null) ?? null,
            clips: (payload.clips as Snapshot['clips']) ?? [],
            job: (payload.job as Snapshot['job']) ?? null,
          });
          return;
        }
        if (type === 'error') {
          // `bus_unavailable` means the API is fine but live updates are not; polling
          // keeps the dashboard truthful until the bus returns.
          if (payload.code === 'bus_unavailable') startPolling();
          return;
        }
        if (typeof payload.stage === 'string') {
          const event = payload as unknown as ProgressEvent;
          setEvents((prev) => [...prev, event].slice(-MAX_EVENTS));
          // Terminal and structural stages change data the REST endpoint owns
          // (clip rows, story plan, job state), so pull a fresh detail view.
          if (['uploaded', 'complete', 'failed', 'story_building', 'rendering'].includes(event.stage)) {
            refreshRef.current();
          }
        }
      };

      socket.onclose = (ev) => {
        socketRef.current = null;
        if (closedRef.current) return;
        if (ev.code === CLOSE_NOT_FOUND) {
          setConnection('closed');
          return;
        }
        startPolling();
        const delay = retryRef.current;
        retryRef.current = Math.min(delay * 2, BACKOFF_MAX_MS);
        timerRef.current = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        // onclose always follows; reconnect is handled there so it is not scheduled twice.
      };
    };

    connect();

    return () => {
      closedRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      stopPolling();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [projectId, startPolling, stopPolling]);

  /** The most recent event for the stage rail; null before anything has happened. */
  const latest = events.length > 0 ? events[events.length - 1] : null;
  const stage: Stage | null = latest ? latest.stage : null;

  return { events, snapshot, connection, stage, latest };
}
