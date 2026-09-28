'use client';

/**
 * The clip inventory: what was uploaded, what ffprobe actually found, and — for anything
 * rejected — the exact reason, inline.
 *
 * Rejected clips are kept in the list on purpose. A row that silently disappears teaches
 * the user nothing; a row that says "unsupported_video_codec: re-encode to H.264" is the
 * difference between a support ticket and a fixed file.
 */

import { formatBytes, formatDuration, type Clip } from '@/lib/api';
import { ISSUE_HINTS, clipStatusMeta } from '@/lib/status';
import { Badge } from './Badge';

export function ClipTable({
  clips,
  onDelete,
  busy,
}: {
  clips: Clip[];
  onDelete: (clipId: string) => void;
  busy: boolean;
}) {
  if (clips.length === 0) {
    return <div className="empty">No clips yet. Upload footage above to get started.</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Resolution</th>
            <th>FPS</th>
            <th>Codec</th>
            <th>Audio</th>
            <th>Size</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {clips.map((clip) => {
            const meta = clipStatusMeta(clip.status);
            const rejected = clip.status === 'rejected';
            return (
              <tr key={clip.id}>
                <td className="wrap">
                  <div title={clip.original_filename}>{clip.original_filename}</div>
                  {clip.duplicate_of && (
                    <div className="muted" style={{ fontSize: 11.5 }}>
                      duplicate — reuses clip {clip.duplicate_of}
                    </div>
                  )}
                  {rejected && clip.rejection_issues && clip.rejection_issues.length > 0 && (
                    <ul className="issues">
                      {clip.rejection_issues.map((issue, i) => (
                        <li key={`${issue.code}-${i}`}>
                          <code>{issue.code}</code>
                          <span>
                            {issue.message}
                            {ISSUE_HINTS[issue.code] && <span className="muted"> {ISSUE_HINTS[issue.code]}</span>}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td>
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                </td>
                <td>{formatDuration(clip.duration)}</td>
                <td>
                  {clip.resolution ?? '—'}
                  {clip.resolution_class && <span className="muted"> {clip.resolution_class}</span>}
                </td>
                <td>{clip.fps ? clip.fps.toFixed(clip.fps % 1 === 0 ? 0 : 2) : '—'}</td>
                <td>{clip.video_codec ?? '—'}</td>
                <td>{clip.has_audio ? `${clip.audio_streams?.length ?? 1} track` : 'none'}</td>
                <td>{formatBytes(clip.file_size_bytes)}</td>
                <td>
                  <button
                    type="button"
                    className="btn danger small"
                    disabled={busy}
                    onClick={() => onDelete(clip.id)}
                    aria-label={`Remove ${clip.original_filename}`}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
