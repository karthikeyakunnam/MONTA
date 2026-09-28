'use client';

/**
 * Drag-and-drop uploader.
 *
 * The dropzone is a real `<button>` wrapping a hidden `<input type="file">`, so keyboard
 * and screen-reader users get the same affordance as a mouse drag — a `div` with an
 * onClick would not. Drag state is tracked with a counter rather than a boolean because
 * `dragleave` fires when the pointer crosses a child element, which makes a naive
 * boolean flicker.
 */

import { useCallback, useRef, useState } from 'react';
import { formatBytes, type UploadLimits } from '@/lib/api';
import { ISSUE_HINTS, isRetryable } from '@/lib/status';
import { useUploadQueue, type UploadItem } from '@/hooks/useUploadQueue';
import { Badge } from './Badge';

export function Uploader({
  projectId,
  limits,
  onSettled,
  disabled,
  disabledReason,
}: {
  projectId: string;
  limits: UploadLimits | null;
  onSettled: () => void;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const queue = useUploadQueue(projectId, limits, onSettled);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = limits ? limits.allowed_extensions.map((e) => `.${e}`).join(',') : 'video/*';

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      if (disabled) return;
      const files = Array.from(event.dataTransfer.files);
      if (files.length > 0) queue.add(files);
    },
    [queue, disabled],
  );

  return (
    <div>
      <button
        type="button"
        className={`dropzone ${dragging ? 'dragging' : ''}`.trim()}
        style={{ width: '100%', opacity: disabled ? 0.55 : 1 }}
        onClick={() => !disabled && inputRef.current?.click()}
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          if (!disabled) setDragging(true);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={(e) => {
          e.preventDefault();
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) setDragging(false);
        }}
        onDrop={onDrop}
        disabled={disabled}
        aria-describedby="upload-rules"
      >
        <div className="big">{dragging ? 'Drop to upload' : 'Drop clips here, or click to choose'}</div>
        <div className="rules" id="upload-rules">
          {disabled && disabledReason
            ? disabledReason
            : limits
              ? `${limits.allowed_extensions.map((e) => e.toUpperCase()).join(' · ')} — up to ${formatBytes(
                  limits.max_file_bytes,
                )} per file, ${limits.max_files_per_request} files at a time. ` +
                limits.resolution_classes
                  .map((c) => `${c.name}: ${c.max_clips} clips / ${Math.round(c.max_total_seconds / 60)} min`)
                  .join(', ')
              : 'Loading upload rules…'}
        </div>
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) queue.add(files);
          // Reset so re-picking the same file fires `change` again.
          e.target.value = '';
        }}
      />

      {queue.items.length > 0 && (
        <>
          <div className="row" style={{ marginTop: 12, justifyContent: 'space-between' }}>
            <span className="muted" style={{ fontSize: 12.5 }}>
              {queue.active > 0 ? `${queue.active} in progress` : 'All uploads settled'}
              {queue.retryable > 0 && ` · ${queue.retryable} can be retried`}
            </span>
            <span className="row" style={{ gap: 6 }}>
              {queue.retryable > 0 && (
                <button type="button" className="btn secondary small" onClick={queue.retryAllFailed}>
                  Retry failed ({queue.retryable})
                </button>
              )}
              {queue.active === 0 && (
                <button type="button" className="btn secondary small" onClick={queue.clearSettled}>
                  Clear list
                </button>
              )}
            </span>
          </div>
          <ul className="queue">
            {queue.items.map((item) => (
              <QueueRow key={item.id} item={item} onRetry={() => queue.retry(item.id)} onCancel={() => queue.cancel(item.id)} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function QueueRow({ item, onRetry, onCancel }: { item: UploadItem; onRetry: () => void; onCancel: () => void }) {
  const pct = item.total > 0 ? Math.min(100, (item.loaded / item.total) * 100) : 0;
  const canRetry = item.status === 'error' || (item.status === 'rejected' && item.issues.some((i) => isRetryable(i.code)));

  return (
    <li>
      <div className="top">
        <span className="name" title={item.file.name}>
          {item.file.name}
        </span>
        <span className="size">{formatBytes(item.file.size)}</span>
        <StatusPill item={item} pct={pct} />
        {(item.status === 'uploading' || item.status === 'queued') && (
          <button type="button" className="btn secondary small" onClick={onCancel}>
            Cancel
          </button>
        )}
        {canRetry && (
          <button type="button" className="btn secondary small" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>

      {(item.status === 'uploading' || item.status === 'queued') && (
        <div className={`bar ${item.serverWork ? 'indeterminate' : ''}`.trim()}>
          <i style={item.serverWork ? undefined : { width: `${pct}%` }} />
        </div>
      )}

      {item.status === 'error' && (
        <div className="issues" style={{ color: 'var(--color-warning)' }}>
          <div style={{ display: 'flex', gap: 7, alignItems: 'baseline', fontSize: 12.5 }}>
            <span>{item.error}</span>
            {item.requestId && <code>{item.requestId}</code>}
          </div>
        </div>
      )}

      {item.issues.length > 0 && (
        <ul className="issues">
          {item.issues.map((issue, i) => (
            <li key={`${issue.code}-${i}`}>
              <code>{issue.code}</code>
              <span>
                {issue.message}
                {ISSUE_HINTS[issue.code] && <span className="muted"> {ISSUE_HINTS[issue.code]}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {item.status === 'accepted' && item.duplicateOf && (
        <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
          Identical to a clip already in this project — stored once and reused, no re-analysis needed.
        </div>
      )}
    </li>
  );
}

function StatusPill({ item, pct }: { item: UploadItem; pct: number }) {
  switch (item.status) {
    case 'queued':
      return <Badge>waiting</Badge>;
    case 'uploading':
      return item.serverWork ? <Badge tone="busy">validating</Badge> : <Badge tone="busy">{pct.toFixed(0)}%</Badge>;
    case 'accepted':
      return <Badge tone="ok">accepted</Badge>;
    case 'rejected':
      return <Badge tone="bad">rejected</Badge>;
    case 'error':
      return <Badge tone="warn">upload failed</Badge>;
  }
}
