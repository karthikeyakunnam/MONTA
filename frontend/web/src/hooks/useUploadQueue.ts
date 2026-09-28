'use client';

/**
 * Upload queue — the client half of Layer 2's ingest contract.
 *
 * Design decisions worth stating:
 *
 * * **One file per HTTP request.** The server accepts batches, but a batch is
 *   all-or-nothing from the browser's point of view: if the connection drops at 90%
 *   every file in it must be resent. One request per file means a retry costs exactly
 *   the file that failed, and progress bars are per-file without parsing a multipart
 *   boundary.
 * * **Bounded concurrency (2).** Video files saturate an uplink; more parallel requests
 *   make every bar slower and none finish sooner. Two keeps the pipe full while one
 *   request is in its handshake or the server is probing.
 * * **Cheap local pre-checks.** Extension and byte size are checked before sending, so
 *   an obviously wrong file fails in milliseconds instead of after a 400 MB upload. They
 *   are *not* a substitute for server validation — the server still verifies real content.
 * * **Retry is only offered where it can succeed.** A transport failure is retryable. A
 *   file rejected for its codec is not: the same bytes produce the same verdict. Quota
 *   rejections are retryable because the user can delete a clip and try again.
 */

import { useCallback, useRef, useState } from 'react';
import {
  ApiError,
  uploadFile,
  type UploadFileResult,
  type UploadLimits,
  type ValidationIssue,
} from '@/lib/api';
import { isRetryable } from '@/lib/status';

export type ItemStatus = 'queued' | 'uploading' | 'accepted' | 'rejected' | 'error';

export interface UploadItem {
  id: string;
  file: File;
  status: ItemStatus;
  /** Bytes confirmed sent by the browser. Never estimated. */
  loaded: number;
  total: number;
  /** True once the bytes are sent and we are waiting on probe/validation server-side. */
  serverWork: boolean;
  issues: ValidationIssue[];
  error: string | null;
  requestId: string | null;
  clipId: string | null;
  duplicateOf: string | null;
  attempts: number;
}

const MAX_PARALLEL = 2;
let counter = 0;
const nextId = () => `u${Date.now().toString(36)}${(counter += 1).toString(36)}`;

function localIssues(file: File, limits: UploadLimits | null): ValidationIssue[] {
  if (!limits) return [];
  const issues: ValidationIssue[] = [];
  const ext = file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : '';
  if (!limits.allowed_extensions.includes(ext)) {
    issues.push({
      code: 'unsupported_extension',
      message: `.${ext || '(none)'} is not a supported video format. Allowed: ${limits.allowed_extensions
        .map((e) => `.${e}`)
        .join(', ')}.`,
    });
  }
  if (file.size === 0) {
    issues.push({ code: 'empty_file', message: 'This file is empty (0 bytes).' });
  } else if (file.size > limits.max_file_bytes) {
    const mb = (limits.max_file_bytes / (1024 * 1024)).toFixed(0);
    issues.push({
      code: 'file_too_large',
      message: `This file is ${(file.size / (1024 * 1024)).toFixed(0)} MB. The limit is ${mb} MB per file.`,
    });
  }
  return issues;
}

export function useUploadQueue(projectId: string, limits: UploadLimits | null, onSettled: () => void) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const inflight = useRef(new Map<string, AbortController>());
  const running = useRef(0);
  const pending = useRef<string[]>([]);
  const itemsRef = useRef<UploadItem[]>([]);
  itemsRef.current = items;

  const patch = useCallback((id: string, changes: Partial<UploadItem>) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...changes } : it)));
  }, []);

  const send = useCallback(
    async (id: string) => {
      const item = itemsRef.current.find((it) => it.id === id);
      if (!item) {
        // The row was cancelled between scheduling and starting: release the slot,
        // otherwise the queue would permanently lose one unit of concurrency.
        running.current -= 1;
        pump();
        return;
      }
      const controller = new AbortController();
      inflight.current.set(id, controller);
      patch(id, { status: 'uploading', loaded: 0, serverWork: false, error: null, issues: [], attempts: item.attempts + 1 });
      try {
        const response = await uploadFile(projectId, item.file, {
          signal: controller.signal,
          onProgress: (loaded, total) => {
            // `loaded === total` means the browser has flushed everything; the server is
            // now hashing and probing. Surfacing that as a distinct state is what keeps a
            // finished bar from looking stuck.
            patch(id, { loaded, total, serverWork: loaded >= total });
          },
        });
        const result: UploadFileResult | undefined = response.results[0];
        if (!result) {
          patch(id, { status: 'error', error: 'The server returned no verdict for this file.', serverWork: false });
          return;
        }
        if (result.accepted) {
          patch(id, {
            status: 'accepted',
            serverWork: false,
            loaded: item.file.size,
            clipId: result.clip?.id ?? null,
            duplicateOf: result.duplicate_of ?? null,
            issues: [],
          });
        } else {
          patch(id, {
            status: 'rejected',
            serverWork: false,
            issues: result.issues ?? [{ code: 'unknown', message: 'Rejected without a reason.' }],
          });
        }
      } catch (error) {
        const api = error instanceof ApiError ? error : null;
        patch(id, {
          status: 'error',
          serverWork: false,
          error: api?.message ?? (error instanceof Error ? error.message : String(error)),
          requestId: api?.requestId ?? null,
        });
      } finally {
        inflight.current.delete(id);
        running.current -= 1;
        onSettled();
        pump();
      }
    },
    [projectId, patch, onSettled],
  );

  /** Start work while there is capacity. Called after every enqueue and every completion. */
  const pump = useCallback(() => {
    while (running.current < MAX_PARALLEL && pending.current.length > 0) {
      const id = pending.current.shift()!;
      const item = itemsRef.current.find((it) => it.id === id);
      if (!item || item.status === 'accepted') continue;
      running.current += 1;
      void send(id);
    }
  }, [send]);

  const add = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      const created: UploadItem[] = files.map((file) => {
        const issues = localIssues(file, limits);
        return {
          id: nextId(),
          file,
          status: issues.length > 0 ? 'rejected' : 'queued',
          loaded: 0,
          total: file.size,
          serverWork: false,
          issues,
          error: null,
          requestId: null,
          clipId: null,
          duplicateOf: null,
          attempts: 0,
        };
      });
      itemsRef.current = [...itemsRef.current, ...created];
      setItems(itemsRef.current);
      pending.current.push(...created.filter((it) => it.status === 'queued').map((it) => it.id));
      pump();
    },
    [limits, pump],
  );

  const retry = useCallback(
    (id: string) => {
      const item = itemsRef.current.find((it) => it.id === id);
      if (!item || item.status === 'uploading' || item.status === 'accepted') return;
      patch(id, { status: 'queued', loaded: 0, error: null, issues: [] });
      pending.current.push(id);
      pump();
    },
    [patch, pump],
  );

  const retryAllFailed = useCallback(() => {
    itemsRef.current
      .filter((it) => it.status === 'error' || (it.status === 'rejected' && it.issues.some((i) => isRetryable(i.code))))
      .forEach((it) => retry(it.id));
  }, [retry]);

  const cancel = useCallback(
    (id: string) => {
      inflight.current.get(id)?.abort();
      pending.current = pending.current.filter((p) => p !== id);
      setItems((prev) => prev.filter((it) => it.id !== id));
    },
    [],
  );

  /** Drop finished rows; leaves anything still in flight or needing attention. */
  const clearSettled = useCallback(() => {
    setItems((prev) => prev.filter((it) => it.status === 'uploading' || it.status === 'queued'));
  }, []);

  const retryable = items.filter(
    (it) => it.status === 'error' || (it.status === 'rejected' && it.issues.some((i) => isRetryable(i.code))),
  ).length;
  const active = items.filter((it) => it.status === 'uploading' || it.status === 'queued').length;

  return { items, add, retry, retryAllFailed, cancel, clearSettled, retryable, active };
}
