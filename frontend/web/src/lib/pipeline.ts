/**
 * When may the pipeline be started?
 *
 * The backend is the authority — it returns 409 if the project is already running and
 * 400 if there is nothing to work with. This mirror exists only to disable the button
 * rather than let the user click into a guaranteed error; the rules must stay in step
 * with `ProjectService.start_pipeline`.
 */

import type { ProjectStatus } from './api';

/** Statuses where a worker is already holding this project. */
export const ACTIVE_PIPELINE_STATUSES: ProjectStatus[] = ['queued', 'analyzing', 'story_building', 'rendering'];

export function canStartPipeline(status: ProjectStatus, usableClips: number): boolean {
  if (usableClips === 0) return false;
  return !ACTIVE_PIPELINE_STATUSES.includes(status);
}
