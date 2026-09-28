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

export default function HomePage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [listFailure, setListFailure] = useState<Failure | null>(null);
  const [title, setTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [platform, setPlatform] = useState('instagram');
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
