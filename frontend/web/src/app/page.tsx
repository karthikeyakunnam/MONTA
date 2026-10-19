'use client';

/**
 * Home — project creation and the project list.
 *
 * Creation is a title plus a free-form prompt, and nothing else: the prompt is the whole
 * input contract for Layer 3, which parses intent out of messy language. Constraining it
 * with dropdowns for genre or pace would replace the thing the system is built to infer.
 * Platform is the one exception — aspect ratio and duration rules are hard facts the
 * user knows and the model should not have to guess.
 */

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { createProject, listProjects, type Project } from '@/lib/api';
import { ErrorBanner, toFailure, type Failure } from '@/components/ErrorBanner';
import { Badge } from '@/components/Badge';
import { projectStatusMeta } from '@/lib/status';

const PLATFORMS: Array<{ value: string; label: string }> = [
  { value: 'instagram', label: 'Instagram (9:16 reel)' },
  { value: 'tiktok', label: 'TikTok (9:16)' },
  { value: 'youtube_short', label: 'YouTube Short (9:16)' },
  { value: 'youtube', label: 'YouTube (16:9)' },
  { value: 'general', label: 'General / no platform rules' },
];

const PROMPT_MAX = 4000;
const PROMPT_MIN = 3;
const COLOR_GRADES = ['natural', 'vibrant', 'warm', 'cool', 'dark', 'orange_teal', 'bw', 'vintage', 'neon', 'high_contrast'];
const CAPTION_STYLES = ['None', 'Minimal', 'Nike', 'Gymshark', 'David Goggins', 'Cinematic Documentary', 'Motivational', 'Alpha Mindset'];
const MUSIC_STYLES = ['epic orchestral', 'hard trap', 'cinematic trailer', 'emotional piano', 'hybrid cinematic', 'sports anthem', 'lo-fi', 'acoustic', 'electronic', 'ambient'];

export default function HomePage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [listFailure, setListFailure] = useState<Failure | null>(null);
  const [title, setTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [platform, setPlatform] = useState('instagram');
  const [duration, setDuration] = useState('');
  const [colorGrade, setColorGrade] = useState('');
  const [captionStyle, setCaptionStyle] = useState('');
  const [musicStyle, setMusicStyle] = useState('');
  const [addMusic, setAddMusic] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [createFailure, setCreateFailure] = useState<Failure | null>(null);

  const load = useCallback(async () => {
    try {
      setProjects(await listProjects());
      setListFailure(null);
    } catch (error) {
      setProjects([]);
      setListFailure(toFailure(error, 'Loading your projects'));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const canSubmit = title.trim().length > 0 && prompt.trim().length >= PROMPT_MIN && !submitting;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setCreateFailure(null);
    try {
      const project = await createProject({
        title: title.trim(),
        prompt: prompt.trim(),
        target_platform: platform,
        edit_options: {
          target_duration_s: duration ? Number(duration) : null,
          color_grade: colorGrade || null,
          caption_style: captionStyle || null,
          music_style: musicStyle || null,
          add_music: addMusic,
        },
      });
      // Straight to the dashboard: the next thing the user needs is the uploader.
      router.push(`/projects/${project.id}`);
    } catch (error) {
      const failure = toFailure(error, 'Creating the project');
      setCreateFailure({ ...failure, onRetry: () => void submit(event) });
      setSubmitting(false);
    }
  };

  return (
    <>
      <h1 className="page-title">New project</h1>
      <p className="page-sub">Name it, tell MONTA what you want, then upload your footage.</p>

      {createFailure && <div style={{ marginTop: 16 }}><ErrorBanner failure={createFailure} onDismiss={() => setCreateFailure(null)} /></div>}

      <form className="card" style={{ marginTop: 16 }} onSubmit={submit}>
        <label className="field">
          <span>Project name</span>
          <input
            type="text"
            value={title}
            maxLength={200}
            placeholder="Bali trip, June"
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </label>

        <label className="field">
          <span>What should MONTA make?</span>
          <textarea
            value={prompt}
            maxLength={PROMPT_MAX}
            placeholder="Make a punchy 30-second reel from my Bali footage. Start with the sunrise, build to the waterfall jump, keep it upbeat and cut on the beat. Nothing slow at the start."
            onChange={(e) => setPrompt(e.target.value)}
            required
          />
          <div className="char-count">
            {prompt.length} / {PROMPT_MAX}
            {prompt.trim().length > 0 && prompt.trim().length < PROMPT_MIN && ' — say a little more'}
          </div>
        </label>

        <label className="field">
          <span>Target platform</span>
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            {PLATFORMS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <details className="edit-options">
          <summary>Fine tune the edit <span className="muted">· duration, look, audio and captions</span></summary>
          <div className="grid cols-2" style={{ marginTop: 14 }}>
            <label className="field">
              <span>Target duration <span className="muted">(optional)</span></span>
              <div className="input-suffix">
                <input type="number" min="5" max="600" step="1" value={duration} placeholder="Choose automatically"
                  onChange={(e) => setDuration(e.target.value)} />
                <span>sec</span>
              </div>
            </label>
            <label className="field">
              <span>Aspect ratio</span>
              <input value={['instagram', 'tiktok', 'youtube_short'].includes(platform) ? '9:16 · vertical' : '16:9 · landscape'} readOnly />
            </label>
            <label className="field">
              <span>Color grade</span>
              <select value={colorGrade} onChange={(e) => setColorGrade(e.target.value)}>
                <option value="">Choose from the footage</option>
                {COLOR_GRADES.map((value) => <option key={value} value={value}>{value.replace('_', ' ')}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Captions</span>
              <select value={captionStyle} onChange={(e) => setCaptionStyle(e.target.value)}>
                <option value="">Use the prompt</option>
                {CAPTION_STYLES.map((value) => <option key={value} value={value}>{value === 'None' ? 'Off' : value}</option>)}
              </select>
              <span className="muted">Burned into the video using local Whisper transcription.</span>
            </label>
            <label className="field">
              <span>Music style</span>
              <select value={musicStyle} onChange={(e) => setMusicStyle(e.target.value)} disabled={!addMusic}>
                <option value="">Choose from the prompt</option>
                {MUSIC_STYLES.map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
            <label className="check-field">
              <span><strong>Generated background music</strong><small>Original clip audio is kept.</small></span>
              <select value={addMusic === null ? 'prompt' : addMusic ? 'on' : 'off'}
                onChange={(e) => setAddMusic(e.target.value === 'prompt' ? null : e.target.value === 'on')}>
                <option value="prompt">Follow the brief</option>
                <option value="on">Add music</option>
                <option value="off">No added music</option>
              </select>
            </label>
          </div>
          <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>Exports are MP4. Platform selection sets the framing and applies that platform’s export profile.</p>
        </details>

        <button type="submit" className="btn" disabled={!canSubmit}>
          {submitting ? 'Creating…' : 'Create project'}
        </button>
      </form>

      <section className="section">
        <div className="section-head">
          <h2>Your projects</h2>
          {projects && <span className="hint">{projects.length} total</span>}
        </div>

        {listFailure && <ErrorBanner failure={{ ...listFailure, onRetry: () => void load() }} />}

        {projects === null ? (
          <div className="empty">Loading…</div>
        ) : projects.length === 0 ? (
          <div className="empty">{listFailure ? 'Could not load projects.' : 'Nothing here yet — create your first project above.'}</div>
        ) : (
          <ul className="project-list">
            {projects.map((project) => {
              const meta = projectStatusMeta(project.status);
              return (
                <li key={project.id}>
                  <Link href={`/projects/${project.id}`}>
                    <div className="title-row">
                      <span className="t">{project.title}</span>
                      <span className="muted" style={{ fontSize: 12.5 }}>
                        {project.clip_count} {project.clip_count === 1 ? 'clip' : 'clips'}
                      </span>
                      <Badge tone={meta.tone}>{meta.label}</Badge>
                    </div>
                    {project.raw_prompt && <div className="prompt">{project.raw_prompt}</div>}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
