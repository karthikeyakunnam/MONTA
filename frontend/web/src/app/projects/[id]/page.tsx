'use client';

/**
 * Project dashboard — upload, inventory, and live pipeline state on one screen.
 *
 * Data flow: REST owns the *facts* (clip rows, metadata, quota, job history) and the
 * websocket owns the *transitions*. The socket never rewrites a clip row from a message
 * payload; when it reports something structural it triggers a refetch, so what the table
 * shows is always what the database holds. That asymmetry is deliberate — it removes the
 * whole class of bug where an optimistic UI and the server disagree about what exists.
 */

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  deleteClip,
  deleteProject,
  formatDuration,
  getProject,
  projectEvents,
  startPipeline,
  uploadLimits,
  type ProgressEvent,
  type ProjectDetail,
  type UploadLimits,
} from '@/lib/api';
import { ACTIVE_PIPELINE_STATUSES, canStartPipeline } from '@/lib/pipeline';
import { Badge } from '@/components/Badge';
import { ClipTable } from '@/components/ClipTable';
import { ErrorBanner, toFailure, type Failure } from '@/components/ErrorBanner';
import { ProgressFeed } from '@/components/ProgressFeed';
import { StatusRail } from '@/components/StatusRail';
import { Uploader } from '@/components/Uploader';
import { useProgressSocket } from '@/hooks/useProgressSocket';
import { jobStateTone, projectStatusMeta } from '@/lib/status';

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const router = useRouter();

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [limits, setLimits] = useState<UploadLimits | null>(null);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);
  const [actionFailure, setActionFailure] = useState<Failure | null>(null);
  const [replayed, setReplayed] = useState<ProgressEvent[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const detail = await getProject(projectId);
      setProject(detail);
      setLoadFailure(null);
    } catch (error) {
      setLoadFailure(toFailure(error, 'Loading this project'));
    }
  }, [projectId]);

  const { events, connection, latest } = useProgressSocket(projectId, () => void refresh());

  useEffect(() => {
    void refresh();
    uploadLimits()
      .then(setLimits)
      // Limits failing is not fatal: the server validates regardless, so the uploader
      // simply loses its client-side pre-check and says so in the dropzone.
      .catch(() => setLimits(null));
    // History over REST as well as over the socket: if the socket cannot open at all,
    // the feed still shows what already happened.
    projectEvents(projectId).then(setReplayed).catch(() => setReplayed([]));
  }, [projectId, refresh]);

  const start = useCallback(async () => {
    setBusy(true);
    setActionFailure(null);
    try {
      await startPipeline(projectId);
      await refresh();
    } catch (error) {
      setActionFailure({ ...toFailure(error, 'Starting processing'), onRetry: () => void start() });
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  const removeClip = useCallback(
    async (clipId: string) => {
      setBusy(true);
      setActionFailure(null);
      try {
        await deleteClip(projectId, clipId);
        await refresh();
      } catch (error) {
        setActionFailure(toFailure(error, 'Removing the clip'));
      } finally {
        setBusy(false);
      }
    },
    [projectId, refresh],
  );

  const removeProject = useCallback(async () => {
    if (!window.confirm('Delete this project and all of its clips? This cannot be undone.')) return;
    setBusy(true);
    try {
      await deleteProject(projectId);
      router.push('/');
    } catch (error) {
      setActionFailure(toFailure(error, 'Deleting the project'));
      setBusy(false);
    }
  }, [projectId, router]);

  if (!project) {
    return (
      <>
        {loadFailure ? (
          <ErrorBanner failure={{ ...loadFailure, onRetry: () => void refresh() }} />
        ) : (
          <div className="empty">Loading project…</div>
        )}
        <Link href="/" className="btn secondary small">
          ← All projects
        </Link>
      </>
    );
  }

  const meta = projectStatusMeta(project.status);
  const usable = project.clips.filter((c) => c.status !== 'rejected');
  const rejected = project.clips.length - usable.length;
  const latestJob = project.jobs[0] ?? null;
  const running = ACTIVE_PIPELINE_STATUSES.includes(project.status);
  const startable = canStartPipeline(project.status, usable.length);

  // Merge the REST replay with live events, de-duplicated: a reconnect can deliver the
  // same event twice and the feed should not stutter.
  const feed = dedupe([...replayed, ...events]);

  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <Link href="/" className="muted" style={{ fontSize: 12.5 }}>
            ← All projects
          </Link>
          <h1 className="page-title" style={{ marginTop: 6 }}>
            {project.title}
          </h1>
          <p className="page-sub">
            <Badge tone={meta.tone}>{meta.label}</Badge>{' '}
            <span className="muted">
              {project.target_platform.replace('_', ' ')} · created{' '}
              {project.created_at ? new Date(project.created_at).toLocaleString() : '—'}
            </span>
          </p>
        </div>
        <div className="row">
          <ConnectionPill connection={connection} />
          <button type="button" className="btn danger small" onClick={() => void removeProject()} disabled={busy}>
            Delete project
          </button>
        </div>
      </div>

      {project.raw_prompt && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="stat">
            <div className="label">Your instruction</div>
          </div>
          <p style={{ marginTop: 6, lineHeight: 1.6 }}>{project.raw_prompt}</p>
        </div>
      )}

      {loadFailure && <div style={{ marginTop: 16 }}><ErrorBanner failure={{ ...loadFailure, onRetry: () => void refresh() }} /></div>}
      {actionFailure && <div style={{ marginTop: 16 }}><ErrorBanner failure={actionFailure} onDismiss={() => setActionFailure(null)} /></div>}
      {project.status === 'failed' && project.status_detail && (
        <div style={{ marginTop: 16 }}>
          <ErrorBanner
            failure={{
              title: 'Processing failed',
              detail: project.status_detail,
              tone: 'error',
              onRetry: () => void start(),
              retryLabel: 'Run again',
            }}
          />
        </div>
      )}
      {latestJob?.state === 'submit_failed' && (
        <div style={{ marginTop: 16 }}>
          <ErrorBanner
            failure={{
              title: 'The job never reached the queue',
              detail:
                latestJob.error ??
                'The broker was unreachable when we tried to schedule the work. Your clips are safe.',
              tone: 'warn',
              onRetry: () => void start(),
              retryLabel: 'Submit again',
            }}
          />
        </div>
      )}

      <section className="section">
        <div className="section-head">
          <h2>Pipeline</h2>
          <span className="hint">
            {connection === 'live'
              ? 'live updates'
              : connection === 'polling'
                ? 'live updates unavailable — polling every 4s'
                : connection === 'connecting'
                  ? 'connecting…'
                  : 'not connected'}
          </span>
        </div>
        <StatusRail status={project.status} latest={latest} clipCount={usable.length} />
        <div className="row" style={{ marginTop: 14 }}>
          <button type="button" className="btn" onClick={() => void start()} disabled={!startable || busy}>
            {running ? 'Processing…' : busy ? 'Working…' : 'Start processing'}
          </button>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {running
              ? 'Work is in flight; the stages above update as the worker reports them.'
              : usable.length === 0
                ? 'Upload at least one usable clip first.'
                : `${usable.length} usable ${usable.length === 1 ? 'clip' : 'clips'}, ${formatDuration(
                    project.total_duration_s,
                  )} of footage.`}
          </span>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Upload footage</h2>
          {rejected > 0 && <span className="hint">{rejected} rejected — reasons shown in the table below</span>}
        </div>
        <Uploader
          projectId={projectId}
          limits={limits}
          onSettled={() => void refresh()}
          disabled={running}
          disabledReason="Uploads are paused while this project is being processed."
        />
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Summary</h2>
        </div>
        <div className="grid cols-3">
          <div className="card stat">
            <div className="label">Usable clips</div>
            <div className="value">{usable.length}</div>
            <div className="foot">{rejected > 0 ? `${rejected} rejected` : 'none rejected'}</div>
          </div>
          <div className="card stat">
            <div className="label">Total footage</div>
            <div className="value">{formatDuration(project.total_duration_s)}</div>
            <div className="foot">{usable.filter((c) => c.has_audio).length} with audio</div>
          </div>
          <div className="card stat">
            <div className="label">Story</div>
            <div className="value">{project.story_pattern ? project.story_pattern.replace(/_/g, ' ') : '—'}</div>
            <div className="foot">
              {project.story_duration_s != null
                ? `${formatDuration(project.story_duration_s)} · ${project.story_cuts ?? 0} cuts`
                : 'not built yet'}
            </div>
          </div>
          <div className="card stat">
            <div className="label">Story score</div>
            <div className="value">{project.story_score != null ? project.story_score.toFixed(2) : '—'}</div>
            <div className="foot">independent judge</div>
          </div>
          <div className="card stat">
            <div className="label">Render</div>
            <div className="value">{project.render_status ?? '—'}</div>
            <div className="foot">
              {project.render_url ? (
                <a href={project.render_url} style={{ color: 'var(--color-accent)' }}>
                  download
                </a>
              ) : (
                'no output yet'
              )}
            </div>
          </div>
          <div className="card stat">
            <div className="label">Latest job</div>
            <div className="value" style={{ fontSize: 15 }}>
              {latestJob ? <Badge tone={jobStateTone(latestJob.state)}>{latestJob.state}</Badge> : '—'}
            </div>
            <div className="foot mono">{latestJob ? latestJob.id : 'never run'}</div>
          </div>
        </div>
      </section>

      {project.quota.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2>Quota</h2>
            <span className="hint">per resolution class, enforced on upload</span>
          </div>
          <div className="grid cols-2">
            {project.quota.map((q) => (
              <div className="card" key={q.resolution_class}>
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <strong>{q.resolution_class}</strong>
                  <span className="muted" style={{ fontSize: 12.5 }}>
                    {q.clips_used}/{q.max_clips} clips · {formatDuration(q.seconds_used)} of{' '}
                    {formatDuration(q.max_seconds)}
                  </span>
                </div>
                <div className="bar" style={{ marginTop: 10 }}>
                  <i style={{ width: `${pct(q.clips_used, q.max_clips)}%` }} />
                </div>
                <div className="bar" style={{ marginTop: 6 }}>
                  <i style={{ width: `${pct(q.seconds_used, q.max_seconds)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2>Clips</h2>
          <span className="hint">metadata read from the file itself by ffprobe</span>
        </div>
        <ClipTable clips={project.clips} onDelete={(id) => void removeClip(id)} busy={busy || running} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Activity</h2>
          <span className="hint">{feed.length} events</span>
        </div>
        <div className="card">
          <ProgressFeed events={feed} />
        </div>
      </section>
    </>
  );
}

function ConnectionPill({ connection }: { connection: string }) {
  if (connection === 'live') return <Badge tone="ok">live</Badge>;
  if (connection === 'polling') return <Badge tone="warn">polling</Badge>;
  if (connection === 'connecting') return <Badge>connecting</Badge>;
  return <Badge tone="bad">offline</Badge>;
}

function pct(used: number, max: number): number {
  if (max <= 0) return 0;
  return Math.max(0, Math.min(100, (used / max) * 100));
}

/** Same event can arrive from the REST replay and the socket; key on time+stage+clip. */
function dedupe(events: ProgressEvent[]): ProgressEvent[] {
  const seen = new Set<string>();
  const out: ProgressEvent[] = [];
  for (const event of events) {
    const key = `${event.at}|${event.stage}|${event.clip_id ?? ''}|${event.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(event);
  }
  return out;
}
