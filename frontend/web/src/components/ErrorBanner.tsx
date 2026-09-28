'use client';

/**
 * One component for every failure the user can see.
 *
 * It distinguishes the four cases the mandate names, because the recovery differs:
 * backend unreachable (start the server / check the URL), queue unavailable (the API
 * is up but work cannot be scheduled — the project is safe), rejected input (fix the
 * file), and a real server fault (quote the request id). The request id is always
 * shown when we have one: without it a user's "it broke" is unsearchable in the logs.
 */

import { ApiError } from '@/lib/api';

export interface Failure {
  title: string;
  detail?: string;
  requestId?: string | null;
  tone?: 'error' | 'warn';
  onRetry?: () => void;
  retryLabel?: string;
}

/** Turn any thrown value into something displayable without losing the diagnostic bits. */
export function toFailure(error: unknown, context: string): Failure {
  if (error instanceof ApiError) {
    if (error.isNetwork) {
      return {
        title: 'Cannot reach the MONTA backend',
        detail: `${error.message} Start it with "make dev-backend" (or check NEXT_PUBLIC_API_BASE).`,
        tone: 'error',
      };
    }
    if (error.status === 503) {
      return {
        title: 'The processing queue is unavailable',
        detail: `${error.message} Your project and clips are saved — start processing again once the queue is back.`,
        requestId: error.requestId,
        tone: 'warn',
      };
    }
    if (error.status === 409) {
      // 409 covers every "not in a state for this" case — already running, no clips yet,
      // editing a project mid-run. The server's message says which, so the title stays
      // neutral rather than asserting one of them.
      return { title: 'Not possible right now', detail: error.message, requestId: error.requestId, tone: 'warn' };
    }
    if (error.status === 404) {
      return { title: 'Not found', detail: error.message, requestId: error.requestId, tone: 'error' };
    }
    if (error.status >= 500) {
      return {
        title: `${context} failed on the server`,
        detail: error.message,
        requestId: error.requestId,
        tone: 'error',
      };
    }
    return { title: `${context} was rejected`, detail: error.message, requestId: error.requestId, tone: 'error' };
  }
  return {
    title: `${context} failed`,
    detail: error instanceof Error ? error.message : String(error),
    tone: 'error',
  };
}

export function ErrorBanner({ failure, onDismiss }: { failure: Failure; onDismiss?: () => void }) {
  return (
    <div className={`banner ${failure.tone ?? 'error'}`} role="alert">
      <div className="body">
        <div className="title">{failure.title}</div>
        {failure.detail && <div className="detail">{failure.detail}</div>}
        {failure.requestId && (
          <div className="detail mono" style={{ marginTop: 6 }}>
            request id: {failure.requestId}
          </div>
        )}
      </div>
      <div className="row" style={{ gap: 6 }}>
        {failure.onRetry && (
          <button type="button" className="btn secondary small" onClick={failure.onRetry}>
            {failure.retryLabel ?? 'Try again'}
          </button>
        )}
        {onDismiss && (
          <button type="button" className="btn secondary small" onClick={onDismiss} aria-label="Dismiss">
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
