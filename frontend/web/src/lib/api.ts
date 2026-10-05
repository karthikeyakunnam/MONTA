/**
 * MONTA — API client
 *
 * One place that knows the backend contract. Every call surfaces a typed
 * `ApiError` carrying the HTTP status, the backend's message and the
 * `X-Request-ID`, so the UI can show something specific and a support request
 * can be traced end to end.
 *
 * Uploads use XMLHttpRequest rather than fetch because only XHR reports real
 * byte-level progress; the progress bars are measured, never animated.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000/api/v1';
const USER_HEADER = 'X-MONTA-User';
const USER_ID = process.env.NEXT_PUBLIC_MONTA_USER ?? 'local';

export type ProjectStatus =
  | 'draft' | 'uploading' | 'queued' | 'analyzing' | 'story_building' | 'rendering' | 'complete' | 'failed';
export type ClipStatus = 'uploaded' | 'analyzing' | 'analyzed' | 'rejected' | 'failed';
export type Stage = 'uploaded' | 'validating' | 'analyzing' | 'story_building' | 'timeline_building' | 'rendering' | 'complete' | 'failed';

export interface ValidationIssue {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

export interface Clip {
  id: string;
  status: ClipStatus;
  filename: string;
  original_filename: string;
  format: string;
  duration: number | null;
  fps: number | null;
  resolution: string | null;
  resolution_class: string | null;
  video_codec: string | null;
  bitrate: number | null;
  file_size_bytes: number;
  has_audio: boolean;
  audio_streams: Array<Record<string, unknown>> | null;
  sha256: string;
  duplicate_of: string | null;
  rejection_issues: ValidationIssue[] | null;
  created_at: string | null;
}

export interface Job {
  id: string;
  kind: string;
  state: 'pending' | 'queued' | 'running' | 'succeeded' | 'failed' | 'submit_failed';
  stage: string | null;
  task_id: string | null;
  attempts: number;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface QuotaClass {
  resolution_class: string;
  clips_used: number;
  max_clips: number;
  seconds_used: number;
  max_seconds: number;
}

export interface Project {
  id: string;
  title: string;
  description: string | null;
  status: ProjectStatus;
  status_detail: string | null;
  raw_prompt: string | null;
  target_platform: string;
  created_at: string | null;
  updated_at: string | null;
  clip_count: number;
}

export interface ProjectDetail extends Project {
  clips: Clip[];
  jobs: Job[];
  quota: QuotaClass[];
  total_duration_s: number;
  story_pattern: string | null;
  story_duration_s: number | null;
  story_cuts: number | null;
  story_score: number | null;
  render_status: string | null;
  render_url: string | null;
}

export interface UploadFileResult {
  filename: string;
  accepted: boolean;
  clip: Clip | null;
  issues: ValidationIssue[] | null;
  duplicate_of: string | null;
}

export interface UploadResponse {
  project_id: string;
  accepted: number;
  rejected: number;
  results: UploadFileResult[];
  project_status: ProjectStatus;
}

export interface UploadLimits {
  allowed_extensions: string[];
  max_file_bytes: number;
  max_request_bytes: number;
  max_files_per_request: number;
  max_clip_seconds: number;
  min_clip_seconds: number;
  resolution_classes: Array<{ name: string; max_pixels: number; max_clips: number; max_total_seconds: number }>;
}

export interface JobSubmission {
  tracking_id: string;
  job_id: string;
  task_id: string | null;
  state: Job['state'];
  project_status: ProjectStatus;
}

export interface ProgressEvent {
  type?: string;
  project_id: string;
  stage: Stage;
  message: string;
  job_id?: string | null;
  clip_id?: string | null;
  percent?: number | null;
  detail?: Record<string, unknown>;
  at: string;
}

export interface Health {
  status: 'ok' | 'degraded';
  database: boolean;
  queue: boolean;
  storage: boolean;
  ffprobe: boolean;
  detail: Record<string, string>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId: string | null = null,
    readonly body: unknown = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** True when the backend itself could not be reached (server down, DNS, CORS, offline). */
  get isNetwork(): boolean {
    return this.status === 0;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        [USER_HEADER]: USER_ID,
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
    });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the MONTA API at ${API_BASE}. Check that the backend is running.`,
      0,
      null,
      cause,
    );
  }
  const requestId = res.headers.get('X-Request-ID');
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const payload = text ? safeJson(text) : null;
  if (!res.ok) {
    throw new ApiError(messageFrom(payload) ?? `Request failed (${res.status})`, res.status, requestId, payload);
  }
  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text.slice(0, 500) };
  }
}

function messageFrom(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const body = payload as Record<string, unknown>;
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail)) {
    // FastAPI validation errors
    return body.detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : undefined;
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .filter(Boolean)
      .join('; ');
  }
  return null;
}

export const health = () => request<Health>('/health');
export const uploadLimits = () => request<UploadLimits>('/upload/limits');
export const listProjects = () => request<Project[]>('/projects');
export const getProject = (id: string) => request<ProjectDetail>(`/projects/${id}`);
export const createProject = (body: {
  title: string;
  prompt: string;
  target_platform?: string;
}) => request<Project>('/projects', { method: 'POST', body: JSON.stringify(body) });
export const updateProject = (id: string, body: { title?: string; prompt?: string; target_platform?: string }) =>
  request<Project>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
export const deleteProject = (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' });
export const deleteClip = (projectId: string, clipId: string) =>
  request<void>(`/upload/${projectId}/${clipId}`, { method: 'DELETE' });
export const startPipeline = (id: string) =>
  request<JobSubmission>(`/projects/${id}/pipeline`, { method: 'POST' });
export const projectEvents = (id: string) => request<ProgressEvent[]>(`/projects/${id}/events`);

/**
 * Upload one file with real progress. Resolves with the server's per-file verdict;
 * rejects with an `ApiError` only when the request itself failed, so the caller can
 * distinguish "rejected by validation" (retry pointless) from "upload failed" (retry sensible).
 */
export function uploadFile(
  projectId: string,
  file: File,
  handlers: {
    onProgress?: (loadedBytes: number, totalBytes: number) => void;
    signal?: AbortSignal;
  } = {},
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('files', file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}/upload/${projectId}`);
    xhr.setRequestHeader(USER_HEADER, USER_ID);
    xhr.responseType = 'text';

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) handlers.onProgress?.(event.loaded, event.total);
    };
    xhr.onload = () => {
      const requestId = xhr.getResponseHeader('X-Request-ID');
      const payload = xhr.responseText ? safeJson(xhr.responseText) : null;
      // 201 accepted, 207 mixed, 422 all rejected: all three carry per-file results.
      if ([201, 207, 422].includes(xhr.status) && payload && (payload as UploadResponse).results) {
        resolve(payload as UploadResponse);
        return;
      }
      reject(new ApiError(messageFrom(payload) ?? `Upload failed (${xhr.status})`, xhr.status, requestId, payload));
    };
    xhr.onerror = () =>
      reject(new ApiError(`Upload failed: cannot reach ${API_BASE}.`, 0, null, null));
    xhr.ontimeout = () => reject(new ApiError('Upload timed out.', 0, null, null));
    xhr.onabort = () => reject(new ApiError('Upload cancelled.', 0, null, null));
    handlers.signal?.addEventListener('abort', () => xhr.abort());
    xhr.send(form);
  });
}

/** WebSocket URL for live progress; derived from API_BASE so one env var configures both. */
export function progressSocketUrl(projectId: string): string {
  const base = new URL(API_BASE, typeof window === 'undefined' ? 'http://localhost' : window.location.href);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  base.pathname = `${base.pathname.replace(/\/$/, '')}/ws/projects/${projectId}`;
  base.search = `?user=${encodeURIComponent(USER_ID)}`;
  return base.toString();
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  const mins = Math.floor(seconds / 60);
  const secs = seconds - mins * 60;
  return mins > 0 ? `${mins}:${secs.toFixed(0).padStart(2, '0')}` : `${secs.toFixed(1)}s`;
}
