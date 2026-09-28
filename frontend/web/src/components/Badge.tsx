import type { Tone } from '@/lib/status';

const CLASS: Record<Tone, string> = {
  neutral: '',
  ok: 'ok',
  busy: 'busy',
  warn: 'warn',
  bad: 'bad',
};

export function Badge({
  tone = 'neutral',
  title,
  children,
}: {
  tone?: Tone;
  /** Hover text; used to spell out *which* dependency is down without widening the pill. */
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <span className={`badge ${CLASS[tone]}`.trim()} title={title}>
      {children}
    </span>
  );
}
