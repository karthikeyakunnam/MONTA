/**
 * MONTA — status vocabulary
 *
 * The backend owns the states; this file owns how they read and look. Keeping the
 * mapping in one place is what stops three screens from disagreeing about whether
 * "analyzing" is blue or orange, and makes an unknown state fail visibly (grey
 * badge with the raw value) instead of rendering blank.
 */

import type { ClipStatus, ProjectStatus, Stage } from './api';

export type Tone = 'neutral' | 'ok' | 'busy' | 'warn' | 'bad';

/** The pipeline as the creator experiences it, in order. Mirrors `Stage` on the backend. */
export const STAGES: Stage[] = [
  'uploaded',
  'validating',
  'analyzing',
  'story_building',
  'timeline_building',
  'rendering',
  'complete',
];

export const STAGE_LABELS: Record<Stage, string> = {
  uploaded: 'Uploaded',
  validating: 'Validating',
  analyzing: 'Analyzing',
  story_building: 'Story building',
  timeline_building: 'Timeline building',
  rendering: 'Rendering',
  complete: 'Complete',
  failed: 'Failed',
};

const PROJECT_STATUS_META: Record<ProjectStatus, { label: string; tone: Tone; stage: Stage | null }> = {
  draft: { label: 'Draft', tone: 'neutral', stage: null },
  uploading: { label: 'Uploading', tone: 'neutral', stage: 'uploaded' },
  queued: { label: 'Queued', tone: 'busy', stage: 'uploaded' },
  analyzing: { label: 'Analyzing', tone: 'busy', stage: 'analyzing' },
  story_building: { label: 'Story building', tone: 'busy', stage: 'story_building' },
  rendering: { label: 'Rendering', tone: 'busy', stage: 'rendering' },
  complete: { label: 'Complete', tone: 'ok', stage: 'complete' },
  failed: { label: 'Failed', tone: 'bad', stage: 'failed' },
};

const CLIP_STATUS_META: Record<ClipStatus, { label: string; tone: Tone }> = {
  uploaded: { label: 'Uploaded', tone: 'neutral' },
  analyzing: { label: 'Analyzing', tone: 'busy' },
  analyzed: { label: 'Analyzed', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'bad' },
  failed: { label: 'Failed', tone: 'bad' },
};

export function projectStatusMeta(status: string) {
  return PROJECT_STATUS_META[status as ProjectStatus] ?? { label: status, tone: 'neutral' as Tone, stage: null };
}

export function clipStatusMeta(status: string) {
  return CLIP_STATUS_META[status as ClipStatus] ?? { label: status, tone: 'neutral' as Tone };
}

export function jobStateTone(state: string): Tone {
  switch (state) {
    case 'succeeded':
      return 'ok';
    case 'running':
    case 'queued':
      return 'busy';
    case 'failed':
    case 'submit_failed':
      return 'bad';
    default:
      return 'neutral';
  }
}

/**
 * Human sentences for the validation codes Layer 2 emits. The backend already sends a
 * message; these are the *action* the creator can take, shown underneath it. An
 * unmapped code simply shows no hint rather than a wrong one.
 */
export const ISSUE_HINTS: Record<string, string> = {
  filename_invalid: 'Rename the file using letters, numbers, dots, dashes or underscores.',
  unsupported_extension: 'Convert it to MP4, MOV or MKV first.',
  unsupported_container: 'The file extension does not match its actual contents.',
  unsupported_video_codec: 'Re-encode to H.264 or HEVC.',
  unreadable_media: 'The file appears corrupt or is not a video. Try re-exporting it.',
  empty_file: 'The file has no contents — the export may have failed.',
  file_too_large: 'Compress the clip or trim it before uploading.',
  clip_too_long: 'Trim the clip before uploading.',
  clip_too_short: 'This clip is too short to be usable in an edit.',
  resolution_too_high: 'Export at a lower resolution, or use fewer 4K clips.',
  project_clip_limit: 'Remove a clip from this project to make room.',
  project_duration_limit: 'Remove or trim clips to fit within the project budget.',
};

/** Rejection is final for these bytes: offering "retry" would just fail identically. */
export function isRetryable(code: string): boolean {
  return code === 'project_clip_limit' || code === 'project_duration_limit';
}
