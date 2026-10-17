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
  type ManualShot,
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
type ManualCaption = { start: number; end: number; text: string };
type ManualDraftSnapshot = { shots: ManualShot[]; musicVolume: number; musicDucking: boolean | null; captions: ManualCaption[] | null };

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
    manual_timeline: options.manual_timeline ?? null,
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
  const [keepManualOverride, setKeepManualOverride] = useState<boolean | null>(null);
  const [manualShots, setManualShots] = useState<ManualShot[] | null>(null);
  const [manualUndo, setManualUndo] = useState<ManualDraftSnapshot[]>([]);
  const [manualRedo, setManualRedo] = useState<ManualDraftSnapshot[]>([]);
  const [manualMusicVolume, setManualMusicVolume] = useState(1);
  const [manualMusicDucking, setManualMusicDucking] = useState<boolean | null>(null);
  const [manualCaptions, setManualCaptions] = useState<ManualCaption[] | null>(null);
  const [compareVersionA, setCompareVersionA] = useState('');
  const [compareVersionB, setCompareVersionB] = useState('');
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
    setKeepManualOverride(null);
    setRevisionOpen(true);
    setActionFailure(null);
  };

  const submitRevision = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (revisionLock.current || !project || !revisionPrompt.trim()) return;
    const manualConflict = Boolean(project.edit_options.manual_timeline && (
      revisionPrompt.trim() !== (project.raw_prompt ?? '').trim()
      || revisionPlatform !== project.target_platform
      || revisionOptions.target_duration_s !== (project.edit_options.target_duration_s ?? null)
    ));
    if (manualConflict && keepManualOverride === null) {
      setActionFailure({ title: 'Resolve the manual timeline',
        detail: 'The brief or duration changed. Choose whether to keep your manual shots or let AI build a new timeline.' });
      return;
    }
    revisionLock.current = true;
    setRevisionBusy(true);
    setActionFailure(null);
    try {
      await createRevision(projectId, {
        prompt: revisionPrompt.trim(), target_platform: revisionPlatform,
        edit_options: { ...revisionOptions, aspect_ratio: revisionOptions.aspect_ratio ?? defaultAspect(revisionPlatform),
          manual_timeline: keepManualOverride === false ? null : revisionOptions.manual_timeline },
        idempotency_key: crypto.randomUUID(),
        expected_prompt_version: project.prompt_version,
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

  const changeManual = (next: ManualShot[]) => {
    if (!manualShots) return;
    setManualUndo((history) => [...history.slice(-29), {
      shots: manualShots, musicVolume: manualMusicVolume, musicDucking: manualMusicDucking, captions: manualCaptions,
    }]);
    setManualRedo([]);
    setManualShots(next);
  };

  const changeManualMusic = (volume: number, ducking: boolean | null) => {
    if (!manualShots) return;
    setManualUndo((history) => [...history.slice(-29), {
      shots: manualShots, musicVolume: manualMusicVolume, musicDucking: manualMusicDucking, captions: manualCaptions,
    }]);
    setManualRedo([]);
    setManualMusicVolume(volume);
    setManualMusicDucking(ducking);
  };

  const changeManualCaptions = (captions: ManualCaption[]) => {
    if (!manualShots || manualCaptions === null) return;
    setManualUndo((history) => [...history.slice(-29), {
      shots: manualShots, musicVolume: manualMusicVolume, musicDucking: manualMusicDucking, captions: manualCaptions,
    }]);
    setManualRedo([]);
    setManualCaptions(captions);
  };

  const startManualEdit = () => {
    if (!timeline) return;
    setManualShots(timeline.timeline.video_segments.map((shot) => ({
      clip_id: shot.clip_id, source_in_ms: shot.source_in_ms, source_out_ms: shot.source_out_ms,
      transition_in: shot.transition_in, transition_in_ms: shot.transition_in_ms, volume: shot.volume,
      fade_in_ms: shot.fade_in_ms, fade_out_ms: shot.fade_out_ms, color: shot.color,
    })));
    setManualUndo([]);
    setManualRedo([]);
    setManualMusicVolume(project?.edit_options.manual_timeline?.music_volume ?? 1);
    setManualMusicDucking(project?.edit_options.manual_timeline?.music_ducking ?? null);
    setManualCaptions(timeline.trace.caption_segments ?? project?.edit_options.manual_timeline?.captions ?? null);
  };

  const updateManualShot = (index: number, change: Partial<ManualShot>) => {
    if (!manualShots) return;
    changeManual(manualShots.map((shot, at) => at === index ? { ...shot, ...change } : shot));
  };

  const submitManualEdit = async () => {
    if (!project || !manualShots || revisionLock.current) return;
    const durations = new Map(project.clips.map((clip) => [clip.id, Math.round((clip.duration ?? 0) * 1000)]));
    if (!manualShots.length || manualShots.some((shot) => shot.source_out_ms <= shot.source_in_ms
      || shot.source_in_ms < 0 || shot.source_out_ms > (durations.get(shot.clip_id) ?? 0))) {
      setActionFailure({ title: 'Check the shot ranges', detail: 'Every shot needs an in point before its out point, within the uploaded clip.' });
      return;
    }
    const editedDuration = (manualShots.reduce((total, shot) => total + shot.source_out_ms - shot.source_in_ms - shot.transition_in_ms, 0)) / 1000;
    if (manualCaptions?.some((caption) => !caption.text.trim() || caption.start < 0
      || caption.end <= caption.start || caption.end > editedDuration)) {
      setActionFailure({ title: 'Check caption text and timing', detail: 'Each caption needs text and a time range inside the edited video.' });
      return;
    }
    if (manualCaptions?.length && project.edit_options.caption_style === 'None') {
      setActionFailure({ title: 'Choose a caption style', detail: 'Captions are off in this project. Set a caption style in Revise edit before rendering manual captions.' });
      return;
    }
    revisionLock.current = true;
    setRevisionBusy(true);
    setActionFailure(null);
    try {
      await createRevision(projectId, {
        prompt: project.raw_prompt ?? '', target_platform: project.target_platform,
        edit_options: { ...optionsForRevision(project.edit_options),
          manual_timeline: { shots: manualShots, music_volume: manualMusicVolume,
            music_ducking: manualMusicDucking, captions: manualCaptions } },
        idempotency_key: crypto.randomUUID(), expected_prompt_version: project.prompt_version,
      });
      setManualShots(null);
      await refresh();
    } catch (error) {
      setActionFailure(toFailure(error, 'Rendering this manual edit'));
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
  const editableSources = usable.filter((clip) => (clip.duration ?? 0) > 0 && clip.status !== 'failed');
  const rejected = project.clips.length - usable.length;
  const latestJob = project.jobs[0] ?? null;
  const activeJob = project.jobs.find((job) => ACTIVE_JOB_STATES.includes(job.state)) ?? null;
  const currentJob = activeJob ?? latestJob;
  const running = ACTIVE_PIPELINE_STATUSES.includes(project.status);
  const startable = canStartPipeline(project.status, usable.length);
  const manualDurationS = manualShots?.reduce((total, shot) => total + shot.source_out_ms - shot.source_in_ms
    - shot.transition_in_ms, 0) ?? 0;

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
  const versionChoices = [...new Set(project.jobs.map((job) => job.prompt_version).filter((v): v is number => v != null))];
  const selectedA = project.jobs.find((job) => String(job.prompt_version) === (compareVersionA || String(project.prompt_version)));
  const selectedB = project.jobs.find((job) => String(job.prompt_version) === (compareVersionB || String(Math.max(1, project.prompt_version - 1))));

  return (
    <div className="studio-page">
      <nav className="row" aria-label="Studio areas" style={{ marginBottom: 14 }}>
        <a className="btn secondary small" href="#create-area">Create</a>
        <a className="btn secondary small" href="#timeline-plan-heading">Edit</a>
        <a className="btn secondary small" href="#render-heading">Export</a>
      </nav>
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
              {project.edit_options.manual_timeline && (
                <label className="field"><span>Manual timeline for this revision</span>
                  <select aria-label="Resolve manual timeline" value={keepManualOverride === null ? '' : keepManualOverride ? 'keep' : 'rebuild'}
                    onChange={(event) => setKeepManualOverride(event.target.value === '' ? null : event.target.value === 'keep')}>
                    <option value="">Choose when changing the brief or duration</option>
                    <option value="keep">Keep my shots and trims</option>
                    <option value="rebuild">Let AI rebuild the shots</option>
                  </select>
                  <small className="muted">Keeping manual shots preserves their order and ranges. Rebuilding uses your new prompt and existing uploads.</small>
                </label>
              )}
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
          <details className="card" style={{ marginTop: 14 }}>
            <summary>Advanced job details · version {activeJob.prompt_version ?? project.prompt_version}</summary>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span>Active job · v{activeJob.prompt_version ?? project.prompt_version} · <span className="mono">{activeJob.id}</span></span>
              <Badge tone={jobStateTone(activeJob.state)}>{activeJob.state}</Badge>
            </div>
            {activeJob.state === 'queued' && (
              <button type="button" className="btn danger small" style={{ marginTop: 10 }} disabled={busy}
                onClick={() => void cancelJob(activeJob)}>Cancel queued revision</button>
            )}
          </details>
        )}
      </section>

      {timeline && (
        <section className="section" aria-labelledby="manual-edit-heading">
          <div className="section-head">
            <div>
              <h2 id="manual-edit-heading">Manual timeline</h2>
              <p className="hint">Adjust saved shots, then render a new version. The current MP4 stays available while it renders.</p>
            </div>
            {!manualShots && <button className="btn secondary small" type="button" onClick={startManualEdit}
              disabled={running || timeline.trace.prompt_version !== project.prompt_version}>Edit shots</button>}
          </div>
          {project.render_url && <video className="render-video" controls preload="metadata"
            src={mediaUrl(project.render_url)} aria-label="Current edit preview" style={{ maxHeight: 540, marginBottom: 12 }} />}
          {manualShots && <div className="card">
            <div className="row" style={{ marginBottom: 12 }}>
              <button className="btn secondary small" type="button" disabled={!manualUndo.length} onClick={() => {
                setManualRedo((history) => [...history, { shots: manualShots,
                  musicVolume: manualMusicVolume, musicDucking: manualMusicDucking, captions: manualCaptions }]);
                const previous = manualUndo[manualUndo.length - 1];
                setManualShots(previous.shots); setManualMusicVolume(previous.musicVolume);
                setManualMusicDucking(previous.musicDucking); setManualCaptions(previous.captions);
                setManualUndo(manualUndo.slice(0, -1));
              }}>Undo</button>
              <button className="btn secondary small" type="button" disabled={!manualRedo.length} onClick={() => {
                setManualUndo((history) => [...history, { shots: manualShots,
                  musicVolume: manualMusicVolume, musicDucking: manualMusicDucking, captions: manualCaptions }]);
                const next = manualRedo[manualRedo.length - 1];
                setManualShots(next.shots); setManualMusicVolume(next.musicVolume);
                setManualMusicDucking(next.musicDucking); setManualCaptions(next.captions);
                setManualRedo(manualRedo.slice(0, -1));
              }}>Redo</button>
              <button className="btn secondary small" type="button" onClick={() => setManualShots(null)}>Discard draft</button>
            </div>
            {manualShots.map((shot, index) => {
              const source = project.clips.find((clip) => clip.id === shot.clip_id);
              return <div className="plan-row" key={`${shot.clip_id}-${index}`}>
                <div className="plan-index">{index + 1}</div>
                <div className="plan-main"><label>Source clip <select aria-label={`Shot ${index + 1} source clip`}
                  value={shot.clip_id} onChange={(event) => {
                    const selected = project.clips.find((clip) => clip.id === event.target.value);
                    updateManualShot(index, { clip_id: event.target.value, source_in_ms: 0,
                      source_out_ms: Math.min(3000, Math.round((selected?.duration ?? 0) * 1000)) });
                  }}>
                  {editableSources.map((clip) => <option key={clip.id} value={clip.id}>{clip.original_filename}</option>)}
                </select></label>
                  <span className="muted">{formatDuration((shot.source_out_ms - shot.source_in_ms) / 1000)} selected</span></div>
                <label className="plan-range">In (s)<input aria-label={`Shot ${index + 1} in point`} type="number" min="0" step="0.1"
                  value={shot.source_in_ms / 1000} onChange={(event) => updateManualShot(index, { source_in_ms: Math.round(Number(event.target.value) * 1000) })} /></label>
                <label className="plan-range">Out (s)<input aria-label={`Shot ${index + 1} out point`} type="number" min="0.1" step="0.1"
                  max={source?.duration ?? undefined} value={shot.source_out_ms / 1000}
                  onChange={(event) => updateManualShot(index, { source_out_ms: Math.round(Number(event.target.value) * 1000) })} /></label>
                <label className="plan-range">Transition<select aria-label={`Shot ${index + 1} transition`} value={shot.transition_in}
                  disabled={index === 0}
                  onChange={(event) => updateManualShot(index, { transition_in: event.target.value,
                    transition_in_ms: event.target.value === 'cut' ? 0 : 250 })}>
                  <option value="cut">Cut</option><option value="crossfade">Crossfade</option>
                  <option value="fade_black">Fade through black</option><option value="fade_white">Fade through white</option>
                </select></label>
                <label className="plan-range">Source audio<input aria-label={`Shot ${index + 1} volume`} type="number" min="0" max="4" step="0.1"
                  value={shot.volume} onChange={(event) => updateManualShot(index, { volume: Number(event.target.value) })} /></label>
                <details className="plan-main"><summary>Shot controls</summary>
                  <label>Transition duration (ms)<input aria-label={`Shot ${index + 1} transition duration`} type="number"
                    min="0" max="2000" step="50" disabled={index === 0 || shot.transition_in === 'cut'}
                    value={shot.transition_in_ms} onChange={(event) => updateManualShot(index, { transition_in_ms: Number(event.target.value) })} /></label>
                  <label>Fade in (ms)<input aria-label={`Shot ${index + 1} fade in`} type="number" min="0" max="2000" step="50"
                    value={shot.fade_in_ms ?? 0} onChange={(event) => updateManualShot(index, { fade_in_ms: Number(event.target.value) })} /></label>
                  <label>Fade out (ms)<input aria-label={`Shot ${index + 1} fade out`} type="number" min="0" max="2000" step="50"
                    value={shot.fade_out_ms ?? 0} onChange={(event) => updateManualShot(index, { fade_out_ms: Number(event.target.value) })} /></label>
                  <label>Brightness<input aria-label={`Shot ${index + 1} brightness`} type="number" min="-1" max="1" step="0.05"
                    value={shot.color?.brightness ?? 0} onChange={(event) => updateManualShot(index, {
                      color: { ...shot.color, brightness: Number(event.target.value) },
                    })} /></label>
                  <label>Saturation<input aria-label={`Shot ${index + 1} saturation`} type="number" min="0" max="3" step="0.05"
                    value={shot.color?.saturation ?? 1} onChange={(event) => updateManualShot(index, {
                      color: { ...shot.color, saturation: Number(event.target.value) },
                    })} /></label>
                </details>
                <div className="row">
                  <button className="btn secondary small" type="button" disabled={index === 0}
                    onClick={() => { const next = [...manualShots]; [next[index - 1], next[index]] = [next[index], next[index - 1]];
                      next[0] = { ...next[0], transition_in: 'cut', transition_in_ms: 0 }; changeManual(next); }}>↑</button>
                  <button className="btn secondary small" type="button" disabled={index === manualShots.length - 1}
                    onClick={() => { const next = [...manualShots]; [next[index], next[index + 1]] = [next[index + 1], next[index]];
                      next[0] = { ...next[0], transition_in: 'cut', transition_in_ms: 0 }; changeManual(next); }}>↓</button>
                  <button className="btn secondary small" type="button" disabled={shot.source_out_ms - shot.source_in_ms < 400}
                    onClick={() => { const midpoint = Math.round((shot.source_in_ms + shot.source_out_ms) / 2);
                      changeManual([...manualShots.slice(0, index), { ...shot, source_out_ms: midpoint },
                        { ...shot, source_in_ms: midpoint, transition_in: 'cut', transition_in_ms: 0 },
                        ...manualShots.slice(index + 1)]); }}>Split</button>
                  <button className="btn secondary small" type="button" disabled={manualShots.length === 1}
                    onClick={() => { const next = manualShots.filter((_, at) => at !== index);
                      next[0] = { ...next[0], transition_in: 'cut', transition_in_ms: 0 }; changeManual(next); }}>Remove</button>
                </div>
              </div>;
            })}
            <button className="btn secondary small" type="button" disabled={manualShots.length >= 100 || editableSources.length === 0}
              onClick={() => { const source = editableSources[0]; changeManual([...manualShots, {
                clip_id: source.id, source_in_ms: 0,
                source_out_ms: Math.min(3000, Math.round((source.duration ?? 0) * 1000)),
                transition_in: 'cut', transition_in_ms: 0, volume: 1,
              }]); }}>Add shot</button>
            <div className="row" style={{ marginTop: 12 }}>
              <label>Music volume <input aria-label="Music volume" type="number" min="0" max="4" step="0.1"
                disabled={timeline.timeline.audio_segments.length === 0}
                value={manualMusicVolume} onChange={(event) => changeManualMusic(Number(event.target.value), manualMusicDucking)} /></label>
              <label>Speech ducking <select aria-label="Speech ducking" disabled={timeline.timeline.audio_segments.length === 0}
                value={manualMusicDucking === null ? '' : manualMusicDucking ? 'on' : 'off'}
                onChange={(event) => changeManualMusic(manualMusicVolume,
                  event.target.value === '' ? null : event.target.value === 'on')}>
                <option value="">Keep current mix</option><option value="on">On</option><option value="off">Off</option>
              </select></label>
            </div>
            {manualCaptions !== null && <details style={{ marginTop: 12 }}>
              <summary>Captions · edit saved transcript</summary>
              {manualCaptions.map((caption, index) => <div className="row" key={index} style={{ marginTop: 8 }}>
                <label>Start (s)<input aria-label={`Caption ${index + 1} start`} type="number" min="0" step="0.1"
                  value={caption.start} onChange={(event) => changeManualCaptions(manualCaptions.map((item, at) =>
                    at === index ? { ...item, start: Number(event.target.value) } : item))} /></label>
                <label>End (s)<input aria-label={`Caption ${index + 1} end`} type="number" min="0.1" step="0.1"
                  value={caption.end} onChange={(event) => changeManualCaptions(manualCaptions.map((item, at) =>
                    at === index ? { ...item, end: Number(event.target.value) } : item))} /></label>
                <label>Text<input aria-label={`Caption ${index + 1} text`} maxLength={500} value={caption.text}
                  onChange={(event) => changeManualCaptions(manualCaptions.map((item, at) =>
                    at === index ? { ...item, text: event.target.value } : item))} /></label>
                <button className="btn secondary small" type="button"
                  onClick={() => changeManualCaptions(manualCaptions.filter((_, at) => at !== index))}>Remove caption</button>
              </div>)}
              <button className="btn secondary small" type="button"
                disabled={(manualCaptions.at(-1)?.end ?? 0) >= manualDurationS / 1000 - 0.1}
                onClick={() => { const start = manualCaptions.at(-1)?.end ?? 0;
                  changeManualCaptions([...manualCaptions, { start, end: Math.min(start + 1, manualDurationS / 1000),
                    text: 'New caption' }]); }}>Add caption</button>
            </details>}
            <p className="hint" style={{ marginTop: 8 }}>{timeline.timeline.audio_segments.length === 0 ? 'Music mix controls unavailable: no external music track is in this edit. ' : ''}Global framing remains in Revise edit. {manualCaptions === null ? 'Caption timing controls unavailable: this version has no saved transcription segments.' : 'Edited captions use the saved transcript and render as burn-in text.'}</p>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="btn" type="button" disabled={revisionBusy || running} onClick={() => void submitManualEdit()}>
                {revisionBusy ? 'Submitting…' : 'Render edited version'}
              </button>
              <span className="hint">Changes persist with the new project version after submission.</span>
            </div>
          </div>}
        </section>
      )}

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
                    <span className="muted">{timeline.trace.shot_decisions?.[index]?.origin === 'user_override' ? 'Manual choice' : 'AI inference'}{timeline.trace.shot_decisions?.[index]?.reason ? ` · ${timeline.trace.shot_decisions[index].reason}` : ''}</span>
                  </div>
                  <div className="plan-range"><span>Source</span>{formatDuration(segment.source_in_ms / 1000)} → {formatDuration(segment.source_out_ms / 1000)}</div>
                  <div className="plan-range"><span>Timeline</span>{formatDuration(segment.timeline_start_ms / 1000)} → {formatDuration(segment.timeline_end_ms / 1000)}</div>
                  <div className="plan-transition">{segment.transition_in === 'cut' ? 'Cut' : `${segment.transition_in.replace('_', ' ')} · ${segment.transition_in_ms}ms`}</div>
                </article>
              );
            })}
            <div className="plan-support">
              {Boolean(timeline.trace.creative_brief?.directives?.length) && <>
                <strong>Brief requirements and preferences</strong>
                {timeline.trace.creative_brief?.directives?.map((directive, index) => <span key={`${directive.category}-${directive.value}-${index}`}>
                  {directive.priority} · {directive.category} · {directive.value} · {directive.origin === 'explicit_setting' ? 'Project setting' : `Prompt: ${directive.source_span}`}
                </span>)}
              </>}
              <strong>Audio</strong>
              {timeline.timeline.audio_segments.length ? timeline.timeline.audio_segments.map((audio) => (
                <span key={audio.track_id}>{audio.track_id} · {formatDuration(audio.timeline_start_ms / 1000)}–{formatDuration(audio.timeline_end_ms / 1000)} · {audio.ducking ? 'waveform ducking' : 'steady mix'} · {audio.volume.toFixed(2)}×</span>
              )) : <span className="muted">No external music segments. Source audio availability is shown per clip below.</span>}
              <strong>Captions</strong>
              {timeline.trace.caption_segments?.length ? timeline.trace.caption_segments.map((caption, index) => (
                <span key={`${caption.start}-${index}`}>{formatDuration(caption.start)}–{formatDuration(caption.end)} · {caption.text}</span>
              )) : <span className="muted">{timeline.trace.caption_error ? `Caption request failed: ${timeline.trace.caption_error}` : timeline.trace.caption_state === 'no_speech_detected' ? 'Whisper ran; no speech segments were found.' : timeline.trace.caption_state === 'requested' ? 'Captions were requested; transcription is still pending.' : 'No rendered caption segments recorded for this timeline.'}</span>}
              {Boolean(timeline.trace.unsupported_effects?.length) && <>
                <strong>Unavailable from this footage or local renderer</strong>
                {timeline.trace.unsupported_effects?.map((effect) => <span key={effect.effect}>
                  {effect.effect.replace(/_/g, ' ')} · {effect.reason}
                </span>)}
              </>}
            </div>
          </div>
        ) : timelineUnavailable ? (
          <div className="empty">No persisted timeline is available yet. Start processing to build a real plan; a timeline from an earlier job may be restored when the worker saves it.</div>
        ) : <div className="empty" role="status">Loading the saved edit plan…</div>}
      </section>

      <section className="section" id="create-area">
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

      <details className="section">
        <summary>Advanced · project diagnostics</summary>
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
            <div className="value">{!timeline?.trace.manual_timeline_applied && project.story_score != null ? project.story_score.toFixed(2) : '—'}</div>
            <div className="foot">{timeline?.trace.manual_timeline_applied ? 'Manual timeline not scored' : 'rule-based estimate, not a human quality rating'}</div>
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
      </details>

      <section className="section" aria-labelledby="version-history-heading">
        <div className="section-head">
          <h2 id="version-history-heading">Compare versions</h2>
          <span className="hint">Each version keeps its prompt, settings, and successful renders.</span>
        </div>
        {versionChoices.length > 0 && <div className="card">
          <div className="row">
            <label>Version A <select aria-label="Compare version A" value={compareVersionA || String(project.prompt_version)}
              onChange={(event) => setCompareVersionA(event.target.value)}>
              {versionChoices.map((version) => <option key={version} value={version}>Version {version}</option>)}
            </select></label>
            <label>Version B <select aria-label="Compare version B" value={compareVersionB || String(Math.max(1, project.prompt_version - 1))}
              onChange={(event) => setCompareVersionB(event.target.value)}>
              {versionChoices.map((version) => <option key={version} value={version}>Version {version}</option>)}
            </select></label>
          </div>
          <div className="grid cols-2" style={{ marginTop: 12 }}>
            {[selectedA, selectedB].map((job, index) => <div key={index}>
              <strong>{index === 0 ? 'A' : 'B'} · {job ? `v${job.prompt_version} · ${job.state}` : 'Unavailable'}</strong>
              <p className="muted" style={{ marginTop: 6 }}>{job?.prompt_snapshot ?? 'No saved prompt.'}</p>
              <small className="muted">{job?.edit_options_snapshot?.manual_timeline ? 'Manual timeline' : 'AI timeline'} · {job?.target_platform_snapshot ?? '—'}</small>
              {(() => { const render = renders.find((candidate) => candidate.preview_url && project.jobs.some(
                (versionJob) => versionJob.id === candidate.job_id && versionJob.prompt_version === job?.prompt_version));
                return render?.preview_url ? <><video className="render-video" controls preload="metadata"
                  aria-label={`${index === 0 ? 'A' : 'B'} version preview`} src={mediaUrl(render.preview_url)}
                  style={{ marginTop: 10, maxHeight: 340 }} /><small className="muted">Saved successful render</small></>
                  : <p className="muted">No successful render for this version.</p>;
              })()}
            </div>)}
          </div>
          {selectedA && selectedB && <p className="settings-diff"><strong>Settings changed:</strong>{' '}
            {changedSettings(selectedB.edit_options_snapshot, selectedA.edit_options_snapshot).join(' · ') || 'none'}</p>}
        </div>}
        <details style={{ marginTop: 12 }}><summary>All jobs and retry details ({project.jobs.length})</summary>
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
        </details>
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
        <details className="section">
          <summary>Advanced · upload quotas</summary>
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
        </details>
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

      <details className="section">
        <summary>Advanced · pipeline events ({feed.length})</summary>
        <div className="card">
          <ProgressFeed events={currentJobFeed} />
        </div>
      </details>
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
    'manual_timeline',
  ];
  return keys.filter((key) => JSON.stringify(previous[key] ?? null) !== JSON.stringify(current[key] ?? null))
    .map((key) => String(key).replace(/_/g, ' '));
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
