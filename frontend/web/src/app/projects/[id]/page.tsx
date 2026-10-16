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

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  deleteClip,
  deleteProject,
  cancelRevision,
  createRevision,
  formatBytes,
  formatDuration,
  getProject,
  getTimeline,
  mediaUrl,
  projectEvents,
  retryRevision,
  startPipeline,
  uploadLimits,
  type ProgressEvent,
  type EditOptions,
  type Job,
  type ProjectDetail,
  type TimelineArtifact,
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

const COLOR_GRADES = ['natural', 'vibrant', 'warm', 'cool', 'dark', 'orange_teal', 'bw', 'vintage', 'neon', 'high_contrast'];
const CAPTION_STYLES = ['None', 'Minimal', 'Nike', 'Gymshark', 'David Goggins', 'Cinematic Documentary', 'Motivational', 'Alpha Mindset'];
const MUSIC_STYLES = ['epic orchestral', 'hard trap', 'cinematic trailer', 'emotional piano', 'hybrid cinematic', 'sports anthem', 'lo-fi', 'acoustic', 'electronic', 'ambient'];
const STYLE_PRESETS = ['cinematic', 'trailer', 'vlog', 'hype', 'montage'];
const ACTIVE_JOB_STATES = ['pending', 'queued', 'running'];
const defaultAspect = (platform: string) => ['instagram', 'tiktok', 'youtube_short'].includes(platform) ? '9:16' : '16:9';

function optionsForRevision(options: EditOptions): EditOptions {
  return {
    target_duration_s: options.target_duration_s ?? null,
    color_grade: options.color_grade ?? null,
    caption_style: options.caption_style ?? null,
    music_style: options.music_style ?? null,
    add_music: options.add_music ?? true,
    aspect_ratio: options.aspect_ratio ?? null,
    style_preset: options.style_preset ?? null,
    pace: options.pace ?? null,
    transition: options.transition ?? null,
    transition_duration_ms: options.transition_duration_ms ?? null,
    scale_mode: options.scale_mode ?? null,
    brightness: options.brightness ?? null,
    contrast: options.contrast ?? null,
    saturation: options.saturation ?? null,
    gamma: options.gamma ?? null,
    shadow_coolness: options.shadow_coolness ?? null,
    highlight_warmth: options.highlight_warmth ?? null,
    vignette: options.vignette ?? null,
  };
}

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const router = useRouter();

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineArtifact | null>(null);
  const [timelineUnavailable, setTimelineUnavailable] = useState(false);
  const [limits, setLimits] = useState<UploadLimits | null>(null);
  const [loadFailure, setLoadFailure] = useState<Failure | null>(null);
  const [actionFailure, setActionFailure] = useState<Failure | null>(null);
  const [replayed, setReplayed] = useState<ProgressEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [revisionBusy, setRevisionBusy] = useState(false);
  const revisionLock = useRef(false);
  const [revisionPrompt, setRevisionPrompt] = useState('');
  const [revisionPlatform, setRevisionPlatform] = useState('instagram');
  const [revisionOptions, setRevisionOptions] = useState<EditOptions>({
    target_duration_s: null, color_grade: null, caption_style: null, music_style: null,
    add_music: true, aspect_ratio: null, style_preset: null, pace: null, transition: null,
    transition_duration_ms: null, scale_mode: null, brightness: null, contrast: null,
    saturation: null, gamma: null, shadow_coolness: null, highlight_warmth: null, vignette: null,
  });

  const refresh = useCallback(async () => {
    try {
      const detail = await getProject(projectId);
      setProject(detail);
      setLoadFailure(null);
      try {
        setTimeline(await getTimeline(projectId));
        setTimelineUnavailable(false);
      } catch {
        setTimeline(null);
        setTimelineUnavailable(true);
      }
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
      await startPipeline(projectId, crypto.randomUUID());
      await refresh();
    } catch (error) {
      setActionFailure({ ...toFailure(error, 'Starting processing'), onRetry: () => void start() });
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  const openRevision = () => {
    if (!project) return;
    setRevisionPrompt(project.raw_prompt ?? '');
    setRevisionPlatform(project.target_platform);
    setRevisionOptions(optionsForRevision(project.edit_options));
    setRevisionOpen(true);
    setActionFailure(null);
  };

  const submitRevision = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (revisionLock.current || !project || !revisionPrompt.trim()) return;
    revisionLock.current = true;
    setRevisionBusy(true);
    setActionFailure(null);
    try {
      await createRevision(projectId, {
        prompt: revisionPrompt.trim(), target_platform: revisionPlatform,
        edit_options: { ...revisionOptions, aspect_ratio: revisionOptions.aspect_ratio ?? defaultAspect(revisionPlatform) },
        idempotency_key: crypto.randomUUID(),
      });
      setRevisionOpen(false);
      await refresh();
    } catch (error) {
      setActionFailure(toFailure(error, 'Submitting this edit revision'));
    } finally {
      revisionLock.current = false;
      setRevisionBusy(false);
    }
  };

  const retryJob = async (job: Job) => {
    setActionFailure(null);
    setBusy(true);
    try {
      await retryRevision(projectId, job.id, crypto.randomUUID());
      await refresh();
    } catch (error) {
      setActionFailure(toFailure(error, 'Retrying this edit revision'));
    } finally {
      setBusy(false);
    }
  };

  const cancelJob = async (job: Job) => {
    setActionFailure(null);
    setBusy(true);
    try {
      await cancelRevision(projectId, job.id);
      await refresh();
    } catch (error) {
      setActionFailure(toFailure(error, 'Cancelling the queued revision'));
    } finally {
      setBusy(false);
    }
  };

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
  const editOptions = project.edit_options ?? {
    target_duration_s: null,
    color_grade: null,
    caption_style: null,
    music_style: null,
    add_music: true,
    aspect_ratio: null,
    style_preset: null, pace: null, transition: null, transition_duration_ms: null,
    scale_mode: null, brightness: null, contrast: null, saturation: null, gamma: null,
    shadow_coolness: null, highlight_warmth: null, vignette: null,
  };
  // Keep the page usable while rolling out the additive render-history schema to
  // a previously running API instance that still returns the older detail shape.
  const renders = project.renders ?? [];
  const usable = project.clips.filter((c) => c.status !== 'rejected');
  const rejected = project.clips.length - usable.length;
  const latestJob = project.jobs[0] ?? null;
  const activeJob = project.jobs.find((job) => ACTIVE_JOB_STATES.includes(job.state)) ?? null;
  const currentJob = activeJob ?? latestJob;
  const running = ACTIVE_PIPELINE_STATUSES.includes(project.status);
  const startable = canStartPipeline(project.status, usable.length);

  // Merge the REST replay with live events, de-duplicated: a reconnect can deliver the
  // same event twice and the feed should not stutter.
  const feed = dedupe([...replayed, ...events]);
  const currentJobFeed = currentJob ? feed.filter((event) => event.job_id === currentJob.id) : [];
  const currentEvent = currentJobFeed[currentJobFeed.length - 1] ??
    (latest?.job_id === currentJob?.id ? latest : null);
  const timelineJobId = timeline?.trace.job_id ?? null;
  const planIsActive = Boolean(activeJob && timelineJobId === activeJob.id);
  const planIsLatest = Boolean(latestJob && timelineJobId === latestJob.id);
  const planHeading = planIsActive ? 'Proposed plan' : planIsLatest ? 'Rendered timeline' : 'Previous rendered timeline';

  return (
    <div className="studio-page">
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
          {project.status_detail && <p className="muted" role="status" style={{ marginTop: 5 }}>{project.status_detail}</p>}
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
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div className="label">Current edit · version {project.prompt_version}</div>
              <p style={{ marginTop: 6, lineHeight: 1.6 }}>{project.raw_prompt}</p>
            </div>
            <button type="button" className="btn secondary small" onClick={openRevision} disabled={running || usable.length === 0}>
              Revise edit
            </button>
          </div>
          {revisionOpen && (
            <form className="edit-options" style={{ marginTop: 16 }} onSubmit={submitRevision}>
              <label className="field">
                <span>Editing prompt</span>
                <textarea value={revisionPrompt} maxLength={4000} required minLength={3}
                  onChange={(event) => setRevisionPrompt(event.target.value)} />
                <small className="muted">Edit the full current instruction. Your existing uploads stay on this project.</small>
              </label>
              <div className="grid cols-2">
                <label className="field">
                  <span>Export target</span>
                  <select value={revisionPlatform} onChange={(event) => {
                    const platform = event.target.value;
                    setRevisionPlatform(platform);
                    setRevisionOptions((current) => ({ ...current, aspect_ratio: defaultAspect(platform) }));
                  }}>
                    <option value="instagram">Instagram</option>
                    <option value="tiktok">TikTok</option>
                    <option value="youtube_short">YouTube Short</option>
                    <option value="youtube">YouTube</option>
                    <option value="general">General</option>
                  </select>
                </label>
                <label className="field">
                  <span>Aspect ratio</span>
                  <select value={revisionOptions.aspect_ratio ?? defaultAspect(revisionPlatform)}
                    onChange={(event) => setRevisionOptions((current) => ({ ...current, aspect_ratio: event.target.value }))}>
                    {['9:16', '16:9', '1:1', '4:5'].map((aspect) => <option key={aspect} value={aspect}>{aspect}</option>)}
                  </select>
                </label>
                <label className="field">
                  <span>Target duration (seconds)</span>
                  <input type="number" min="5" max="600" step="1"
                    value={revisionOptions.target_duration_s ?? ''} placeholder="Automatic"
                    onChange={(event) => setRevisionOptions((current) => ({
                      ...current, target_duration_s: event.target.value ? Number(event.target.value) : null,
                    }))} />
                </label>
                <label className="field">
                  <span>Color style</span>
                  <select value={revisionOptions.color_grade ?? ''} onChange={(event) => setRevisionOptions((current) => ({
                    ...current, color_grade: event.target.value || null,
                  }))}>
                    <option value="">Infer from prompt</option>
                    {COLOR_GRADES.map((style) => <option key={style} value={style}>{style.replace('_', ' ')}</option>)}
                  </select>
                </label>
                <label className="field">
                  <span>Captions</span>
                  <select value={revisionOptions.caption_style ?? ''} onChange={(event) => setRevisionOptions((current) => ({
                    ...current, caption_style: event.target.value || null,
                  }))}>
                    <option value="">Infer from prompt</option>
                    {CAPTION_STYLES.map((style) => <option key={style} value={style}>{style === 'None' ? 'Off' : style}</option>)}
                  </select>
                  <small className="muted">Requested captions use the configured local Whisper model.</small>
                </label>
                <label className="field">
                  <span>Music style</span>
                  <select value={revisionOptions.music_style ?? ''} disabled={!revisionOptions.add_music}
                    onChange={(event) => setRevisionOptions((current) => ({ ...current, music_style: event.target.value || null }))}>
                    <option value="">Infer from prompt</option>
                    {MUSIC_STYLES.map((style) => <option key={style} value={style}>{style}</option>)}
                  </select>
                </label>
                <label className="check-field">
                  <input type="checkbox" checked={revisionOptions.add_music}
                    onChange={(event) => setRevisionOptions((current) => ({ ...current, add_music: event.target.checked }))} />
                  <span><strong>Add generated music</strong><small>Mixes with source audio using the existing soundtrack stage.</small></span>
                </label>
              </div>
              <details className="advanced-controls">
                <summary>Advanced creative controls <span className="muted"> · style, grade, pacing, framing</span></summary>
                <div className="grid cols-2 advanced-grid">
                  <label className="field">
                    <span>Style preset</span>
                    <select value={revisionOptions.style_preset ?? ''} onChange={(event) => setRevisionOptions((current) => ({ ...current, style_preset: event.target.value || null }))}>
                      <option value="">Use prompt and project style</option>
                      {STYLE_PRESETS.map((preset) => <option key={preset} value={preset}>{styleLabel(preset)}</option>)}
                    </select>
                    <small className="muted">Preset sets a concrete base grade and transition style. Controls below override it.</small>
                  </label>
                  <label className="field">
                    <span>Cut pace</span>
                    <select value={revisionOptions.pace ?? ''} onChange={(event) => setRevisionOptions((current) => ({ ...current, pace: event.target.value || null }))}>
                      <option value="">Follow creative brief</option>
                      <option value="slow">Slow</option><option value="medium">Balanced</option>
                      <option value="fast">Fast</option><option value="aggressive">Aggressive</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>Transition</span>
                    <select value={revisionOptions.transition ?? ''} onChange={(event) => setRevisionOptions((current) => ({ ...current, transition: event.target.value || null }))}>
                      <option value="">Style default</option>
                      {['cut', 'crossfade', 'fade_black', 'fade_white', 'wipe_left', 'wipe_right', 'slide_left', 'slide_right'].map((transition) => <option key={transition} value={transition}>{transition.replace('_', ' ')}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span>Transition duration · milliseconds</span>
                    <input type="number" min="0" max="2000" step="25" value={revisionOptions.transition_duration_ms ?? ''} placeholder="Style default"
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, transition_duration_ms: event.target.value ? Number(event.target.value) : null }))} />
                  </label>
                  <label className="field">
                    <span>Composition</span>
                    <select value={revisionOptions.scale_mode ?? ''} onChange={(event) => setRevisionOptions((current) => ({ ...current, scale_mode: (event.target.value || null) as EditOptions['scale_mode'] }))}>
                      <option value="">Automatic</option><option value="fill">Fill and crop</option>
                      <option value="fit">Fit with padding</option><option value="stretch">Stretch</option>
                    </select>
                    <small className="muted">Detected baked-in borders are cropped before canvas fitting.</small>
                  </label>
                  <label className="field range-field">
                    <span>Brightness <output>{(revisionOptions.brightness ?? 0).toFixed(2)}</output></span>
                    <input aria-label="Brightness" type="range" min="-0.3" max="0.3" step="0.01" value={revisionOptions.brightness ?? 0}
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, brightness: Number(event.target.value) }))} />
                  </label>
                  <label className="field range-field">
                    <span>Contrast <output>{(revisionOptions.contrast ?? 1).toFixed(2)}</output></span>
                    <input aria-label="Contrast" type="range" min="0.5" max="2" step="0.01" value={revisionOptions.contrast ?? 1}
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, contrast: Number(event.target.value) }))} />
                  </label>
                  <label className="field range-field">
                    <span>Saturation <output>{(revisionOptions.saturation ?? 1).toFixed(2)}</output></span>
                    <input aria-label="Saturation" type="range" min="0" max="2" step="0.01" value={revisionOptions.saturation ?? 1}
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, saturation: Number(event.target.value) }))} />
                  </label>
                  <label className="field range-field">
                    <span>Gamma <output>{(revisionOptions.gamma ?? 1).toFixed(2)}</output></span>
                    <input aria-label="Gamma" type="range" min="0.6" max="1.6" step="0.01" value={revisionOptions.gamma ?? 1}
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, gamma: Number(event.target.value) }))} />
                  </label>
                  <label className="field range-field">
                    <span>Cool shadows <output>{(revisionOptions.shadow_coolness ?? 0).toFixed(2)}</output></span>
                    <input aria-label="Cool shadows" type="range" min="-1" max="1" step="0.01" value={revisionOptions.shadow_coolness ?? 0}
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, shadow_coolness: Number(event.target.value) }))} />
                  </label>
                  <label className="field range-field">
                    <span>Warm highlights <output>{(revisionOptions.highlight_warmth ?? 0).toFixed(2)}</output></span>
                    <input aria-label="Warm highlights" type="range" min="-1" max="1" step="0.01" value={revisionOptions.highlight_warmth ?? 0}
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, highlight_warmth: Number(event.target.value) }))} />
                  </label>
                  <label className="field range-field">
                    <span>Vignette <output>{(revisionOptions.vignette ?? 0).toFixed(2)}</output></span>
                    <input aria-label="Vignette" type="range" min="0" max="1" step="0.01" value={revisionOptions.vignette ?? 0}
                      onChange={(event) => setRevisionOptions((current) => ({ ...current, vignette: Number(event.target.value) }))} />
                  </label>
                </div>
                <p className="control-note">Speech-energy audio ducking is available in the soundtrack mix. Semantic speech timing, beat-aligned cuts, and tracked subject masks are unavailable in this local pipeline.</p>
              </details>
              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <button type="button" className="btn secondary" onClick={() => setRevisionOpen(false)} disabled={revisionBusy}>Keep current version</button>
                <button type="submit" className="btn" disabled={revisionBusy || running || usable.length === 0}>
                  {revisionBusy ? 'Submitting revision…' : 'Create new version'}
                </button>
              </div>
            </form>
          )}
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
              onRetry: latestJob ? () => void retryJob(latestJob) : undefined,
              retryLabel: 'Retry this revision',
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
              onRetry: latestJob ? () => void retryJob(latestJob) : undefined,
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
        <StatusRail status={project.status} latest={currentEvent} clipCount={usable.length} />
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
        {activeJob && (
          <div className="card" style={{ marginTop: 14 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span>Active job · v{activeJob.prompt_version ?? project.prompt_version} · <span className="mono">{activeJob.id}</span></span>
              <Badge tone={jobStateTone(activeJob.state)}>{activeJob.state}</Badge>
            </div>
            {activeJob.state === 'queued' && (
              <button type="button" className="btn danger small" style={{ marginTop: 10 }} disabled={busy}
                onClick={() => void cancelJob(activeJob)}>Cancel queued revision</button>
            )}
          </div>
        )}
      </section>

      <section className="section" aria-labelledby="timeline-plan-heading">
        <div className="section-head">
          <div>
            <h2 id="timeline-plan-heading">{timeline ? planHeading : 'Edit plan'}</h2>
            <p className="hint">Canonical Timeline IR · shot ranges and audio treatment from the saved pipeline plan</p>
          </div>
          {timeline && <span className="hint">{timeline.timeline.width} × {timeline.timeline.height} · {formatDuration(timeline.timeline.total_duration_ms / 1000)}</span>}
        </div>
        {timeline ? (
          <div className="plan-list">
            {timeline.timeline.video_segments.map((segment, index) => {
              const source = project.clips.find((clip) => clip.id === segment.clip_id);
              return (
                <article className="plan-row" key={segment.segment_id}>
                  <div className="plan-index">{String(index + 1).padStart(2, '0')}</div>
                  <div className="plan-main">
                    <strong>{source?.original_filename ?? segment.clip_id}</strong>
                    <span className="muted">{segment.narrative_role ?? segment.act_id ?? 'Story shot'}{segment.emotional_stage ? ` · ${segment.emotional_stage}` : ''}{segment.is_hero ? ' · hero shot' : ''}</span>
                  </div>
                  <div className="plan-range"><span>Source</span>{formatDuration(segment.source_in_ms / 1000)} → {formatDuration(segment.source_out_ms / 1000)}</div>
                  <div className="plan-range"><span>Timeline</span>{formatDuration(segment.timeline_start_ms / 1000)} → {formatDuration(segment.timeline_end_ms / 1000)}</div>
                  <div className="plan-transition">{segment.transition_in === 'cut' ? 'Cut' : `${segment.transition_in.replace('_', ' ')} · ${segment.transition_in_ms}ms`}</div>
                </article>
              );
            })}
            <div className="plan-support">
              <strong>Audio</strong>
              {timeline.timeline.audio_segments.length ? timeline.timeline.audio_segments.map((audio) => (
                <span key={audio.track_id}>{audio.track_id} · {formatDuration(audio.timeline_start_ms / 1000)}–{formatDuration(audio.timeline_end_ms / 1000)} · {audio.ducking ? 'waveform ducking' : 'steady mix'} · {audio.volume.toFixed(2)}×</span>
              )) : <span className="muted">No external music segments. Source audio availability is shown per clip below.</span>}
              <strong>Captions</strong>
              {timeline.trace.caption_segments?.length ? timeline.trace.caption_segments.map((caption, index) => (
                <span key={`${caption.start}-${index}`}>{formatDuration(caption.start)}–{formatDuration(caption.end)} · {caption.text}</span>
              )) : <span className="muted">{timeline.trace.caption_error ? `Caption request failed: ${timeline.trace.caption_error}` : timeline.trace.caption_state === 'no_speech_detected' ? 'Whisper ran; no speech segments were found.' : timeline.trace.caption_state === 'requested' ? 'Captions were requested; transcription is still pending.' : 'No rendered caption segments recorded for this timeline.'}</span>}
            </div>
          </div>
        ) : timelineUnavailable ? (
          <div className="empty">No persisted timeline is available yet. Start processing to build a real plan; a timeline from an earlier job may be restored when the worker saves it.</div>
        ) : <div className="empty" role="status">Loading the saved edit plan…</div>}
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
            <div className="label">Heuristic plan score</div>
            <div className="value">{project.story_score != null ? project.story_score.toFixed(2) : '—'}</div>
            <div className="foot">rule-based estimate, not a human quality rating</div>
          </div>
          <div className="card stat">
            <div className="label">Render</div>
            <div className="value">{project.render_status ?? '—'}</div>
            <div className="foot">{project.render_resolution ?? 'MP4'} · {project.render_duration_s != null ? formatDuration(project.render_duration_s) : 'no output yet'}</div>
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

      <section className="section" aria-labelledby="version-history-heading">
        <div className="section-head">
          <h2 id="version-history-heading">Version history</h2>
          <span className="hint">Each job keeps its prompt and editing options.</span>
        </div>
        {project.jobs.length ? (
          <div className="grid">
            {project.jobs.map((job) => {
              const isActive = job.id === activeJob?.id;
              const isCurrent = job.prompt_version === project.prompt_version;
              const snapshot = job.edit_options_snapshot;
              const canRetry = !running && isCurrent && ['failed', 'submit_failed'].includes(job.state);
              return (
                <article className="card" key={job.id}>
                  <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                    <strong>Version {job.prompt_version ?? '—'}{job.retry_of_job_id ? ' · retry' : ''}</strong>
                    <div className="row">
                      {isActive && <Badge tone="busy">active</Badge>}
                      {!isCurrent && <Badge tone="neutral">previous</Badge>}
                      <Badge tone={jobStateTone(job.state)}>{job.state}</Badge>
                    </div>
                  </div>
                  <p style={{ margin: '10px 0', lineHeight: 1.5 }}>
                    {job.prompt_snapshot ?? 'Prompt history unavailable for this older job.'}
                  </p>
                  <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
                    {job.target_platform_snapshot ?? '—'} · {snapshot?.target_duration_s ? `${snapshot.target_duration_s}s` : 'automatic duration'} ·
                    {' '}{snapshot?.aspect_ratio ?? 'platform aspect'} · {snapshot?.style_preset ?? snapshot?.color_grade ?? 'inferred look'} ·
                    {' '}{snapshot?.pace ?? 'brief-led pace'} · {snapshot?.transition ?? 'style transition'} · {snapshot?.scale_mode ?? 'automatic framing'} ·
                    {' '}{snapshot?.add_music ? `music${snapshot.music_style ? ` · ${snapshot.music_style}` : ''}` : 'source audio'} ·
                    {' '}{snapshot?.caption_style ?? 'captions from prompt'}
                  </div>
                  {changedSettings(project.jobs.find((candidate) => candidate.prompt_version === (job.prompt_version ?? 0) - 1)?.edit_options_snapshot, snapshot).length > 0 && (
                    <p className="settings-diff"><strong>Changed settings:</strong> {changedSettings(project.jobs.find((candidate) => candidate.prompt_version === (job.prompt_version ?? 0) - 1)?.edit_options_snapshot, snapshot).join(' · ')}</p>
                  )}
                  {job.revision_mode === 'incremental' && (
                    <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>
                      Targeted revision · kept {job.inherited_requirements.length
                        ? job.inherited_requirements.join(', ')
                        : 'the prior edit requirements'}.
                    </p>
                  )}
                  <div className="row" style={{ justifyContent: 'space-between', marginTop: 10 }}>
                    <span className="mono muted" style={{ fontSize: 11.5 }}>{job.id}</span>
                    <span className="muted" style={{ fontSize: 12 }}>
                      {job.created_at ? new Date(job.created_at).toLocaleString() : '—'}{job.stage ? ` · ${job.stage}` : ''}
                    </span>
                  </div>
                  {job.error && <p className="muted" role="status" style={{ marginTop: 8 }}>{job.error}</p>}
                  {canRetry && <button type="button" className="btn secondary small" disabled={busy}
                    style={{ marginTop: 10 }} onClick={() => void retryJob(job)}>Retry with saved inputs</button>}
                </article>
              );
            })}
          </div>
        ) : <div className="empty">No processing jobs yet.</div>}
      </section>

      <section className="section" aria-labelledby="render-heading">
        <div className="section-head">
          <h2 id="render-heading">Final video</h2>
          {project.render_size_bytes != null && <span className="hint">{formatBytes(project.render_size_bytes)} MP4</span>}
        </div>
        {project.render_url ? (
          <div className="card render-card">
            <video className="render-video" controls preload="metadata" src={mediaUrl(project.render_url)}
              aria-label={`Preview of the rendered video for ${project.title}`} />
            <div className="row render-meta">
              <span>{project.render_resolution ?? 'MP4'}</span>
              <span>{formatDuration(project.render_duration_s)}</span>
              {project.render_size_bytes != null && <span>{formatBytes(project.render_size_bytes)}</span>}
              <a className="btn small" href={`${mediaUrl(project.render_url)}?download=true`} download>
                Download MP4
              </a>
            </div>
          </div>
        ) : project.render_status === 'complete' ? (
          <div className="empty" role="status">The job completed, but its MP4 is not available in storage. Run processing again to create a new export.</div>
        ) : (
          <div className="empty">The rendered MP4 will appear here when processing finishes.</div>
        )}
      </section>

      {renders.length > 0 && (
        <section className="section" aria-labelledby="render-history-heading">
          <div className="section-head">
            <h2 id="render-history-heading">Generated renders</h2>
            <span className="hint">Previous successful exports stay available while revisions run or fail.</span>
          </div>
          <div className="grid cols-2">
            {renders.map((render) => {
              const renderJob = project.jobs.find((job) => job.id === render.job_id);
              return (
                <article className="card" key={render.id}>
                  <div className="row" style={{ justifyContent: 'space-between' }}>
                    <strong>{render.is_current ? 'Current render' : 'Previous render'} · {render.platform ? `${render.platform} export` : 'Master render'} · v{renderJob?.prompt_version ?? '—'}</strong>
                    <Badge tone={render.status === 'complete' ? 'ok' : 'warn'}>{render.status}</Badge>
                  </div>
                  {render.preview_url ? (
                    <video className="render-video" controls preload="metadata" src={mediaUrl(render.preview_url)}
                      aria-label={`Preview render ${render.id}`} style={{ marginTop: 12 }} />
                  ) : <div className="empty">This older file is unavailable in storage.</div>}
                  <div className="row render-meta">
                    <span>{render.resolution}</span>
                    <span>{render.duration != null ? formatDuration(render.duration) : '—'}</span>
                    {render.file_size_bytes != null && <span>{formatBytes(render.file_size_bytes)}</span>}
                    {render.preview_url && <a className="btn small" href={`${mediaUrl(render.preview_url)}?download=true`} download>Download MP4</a>}
                  </div>
                  {renderJob?.prompt_snapshot && <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>{renderJob.prompt_snapshot}</p>}
                </article>
              );
            })}
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-head"><h2>Edit settings</h2></div>
        <div className="card settings-summary">
          <span>{platformAspect(project.target_platform)}</span>
          <span>{editOptions.target_duration_s ? `${editOptions.target_duration_s}s target` : 'Automatic duration'}</span>
          <span>{editOptions.color_grade ?? 'Color selected from footage'}</span>
          <span>{!editOptions.caption_style ? 'Captions from prompt' : editOptions.caption_style === 'None' ? 'Captions off' : `${editOptions.caption_style} captions`}</span>
          <span>{editOptions.add_music ? `Music on · ${editOptions.music_style ?? 'from prompt'}` : 'Original audio only'}</span>
          <span>H.264 MP4 · {aspectResolution(editOptions.aspect_ratio ?? platformAspectRatio(project.target_platform))}</span>
        </div>
        <p className="hint" style={{ marginTop: 8 }}>Available export canvas presets: 1080 × 1920, 1920 × 1080, 1080 × 1080, and 1080 × 1350. Platform target and aspect are saved with each revision.</p>
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
        {usable.length > 0 && <div className="media-gallery">
          {usable.map((clip) => (
            <article className="media-card" key={clip.id}>
              {clip.preview_url ? <video muted playsInline preload="metadata" src={mediaUrl(clip.preview_url)}
                aria-label={`Preview of ${clip.original_filename}`} onLoadedMetadata={(event) => {
                  const video = event.currentTarget;
                  if (video.duration > 0.5) video.currentTime = 0.5;
                }} /> : <div className="media-placeholder">Preview unavailable</div>}
              <strong title={clip.original_filename}>{clip.original_filename}</strong>
              <span>{formatDuration(clip.duration)} · {clip.resolution ?? 'resolution unknown'}</span>
              <span>{clip.has_audio ? 'Audio available' : 'No source audio'} · {clip.video_codec ?? 'codec unknown'}</span>
            </article>
          ))}
        </div>}
        <ClipTable clips={project.clips} onDelete={(id) => void removeClip(id)} busy={busy || running} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Activity</h2>
          <span className="hint">{feed.length} events</span>
        </div>
        <div className="card">
          <ProgressFeed events={currentJobFeed} />
        </div>
      </section>
    </div>
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

function platformAspect(platform: string): string {
  return ['instagram', 'tiktok', 'youtube_short'].includes(platform)
    ? '9:16 · vertical'
    : '16:9 · landscape';
}

function platformAspectRatio(platform: string): string {
  return ['instagram', 'tiktok', 'youtube_short'].includes(platform) ? '9:16' : '16:9';
}

function aspectResolution(aspect: string): string {
  const sizes: Record<string, string> = {
    '9:16': '1080 × 1920', '16:9': '1920 × 1080', '1:1': '1080 × 1080', '4:5': '1080 × 1350',
  };
  return sizes[aspect] ?? aspect;
}

function styleLabel(preset: string): string {
  const labels: Record<string, string> = {
    cinematic: 'Cinematic · orange teal grade, dissolve',
    trailer: 'Trailer · dark contrast, hard cuts',
    vlog: 'Vlog · vibrant grade, dissolve',
    hype: 'Hype · high contrast, wipe transition',
    montage: 'Montage · natural grade, cuts',
  };
  return labels[preset] ?? preset;
}

function changedSettings(previous: EditOptions | null | undefined, current: EditOptions | null | undefined): string[] {
  if (!current) return [];
  if (!previous) return [];
  const keys: Array<keyof EditOptions> = [
    'target_duration_s', 'aspect_ratio', 'color_grade', 'style_preset', 'pace', 'transition',
    'transition_duration_ms', 'scale_mode', 'brightness', 'contrast', 'saturation', 'gamma',
    'shadow_coolness', 'highlight_warmth', 'vignette', 'music_style', 'add_music', 'caption_style',
  ];
  return keys.filter((key) => (previous[key] ?? null) !== (current[key] ?? null)).map((key) => String(key).replace(/_/g, ' '));
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
