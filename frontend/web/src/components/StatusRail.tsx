'use client';

/**
 * The seven-state pipeline rail.
 *
 * State is derived from the project's persisted status and the last real event, never
 * from a timer. A stage is `done` only when the pipeline has demonstrably moved past it;
 * the current stage shows the producer's own message, and a percentage appears only for
 * the stage that actually reported one.
 */

import { STAGES, STAGE_LABELS, projectStatusMeta } from '@/lib/status';
import type { ProgressEvent, ProjectStatus, Stage } from '@/lib/api';

function stageIndex(stage: Stage | null): number {
  if (!stage) return -1;
  return STAGES.indexOf(stage);
}

export function StatusRail({
  status,
  latest,
  clipCount,
}: {
  status: ProjectStatus;
  latest: ProgressEvent | null;
  clipCount: number;
}) {
  const failed = status === 'failed' || latest?.stage === 'failed';
  const meta = projectStatusMeta(status);
  // The event stream is more current than the row we last fetched, so it wins when both
  // name a stage; the row is the fallback for a page loaded with no live events yet.
  const currentStage: Stage | null =
    latest && latest.stage !== 'failed' ? latest.stage : (meta.stage as Stage | null);
  const current = stageIndex(currentStage);

  return (
    <ul className="rail">
      {STAGES.map((stage, i) => {
        const isCurrent = i === current && status !== 'complete';
        const cls = failed && i === current ? 'failed' : isCurrent ? 'active' : i <= current ? 'done' : '';
        return (
          <li key={stage} className={cls}>
            <div className="step">{STAGE_LABELS[stage]}</div>
            <div className="state">{stateText(stage, i, current, failed, status, clipCount)}</div>
            {isCurrent && !failed && (
              <div style={{ marginTop: 7 }}>
                {typeof latest?.percent === 'number' ? (
                  <>
                    <div className="bar">
                      <i style={{ width: `${Math.max(2, Math.min(100, latest.percent))}%` }} />
                    </div>
                    <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                      {latest.percent.toFixed(0)}%
                    </div>
                  </>
                ) : (
                  <div className="bar indeterminate">
                    <i />
                  </div>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function stateText(
  stage: Stage,
  index: number,
  current: number,
  failed: boolean,
  status: ProjectStatus,
  clipCount: number,
): string {
  if (failed && index === current) return 'Failed';
  if (index < current) return 'Done';
  if (index === current) {
    if (stage === 'complete') return 'Done';
    if (stage === 'uploaded' && status === 'queued') return 'Queued';
    return 'In progress';
  }
  if (stage === 'uploaded' && clipCount === 0) return 'No clips yet';
  return 'Waiting';
}
