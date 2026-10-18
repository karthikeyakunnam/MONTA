import { execFileSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

const projectId = process.env.MONTA_LIVE_SOURCE_TRIM_PROJECT_ID;
test.skip(!projectId, 'Set MONTA_LIVE_SOURCE_TRIM_PROJECT_ID to a completed local project.');

test('browser trim, undo, redo, save and reload reach a real MP4', async ({ page }, testInfo) => {
  test.setTimeout(9 * 60 * 1000);
  const api = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000/api/v1';
  const headers = { 'X-MONTA-User': process.env.NEXT_PUBLIC_MONTA_USER ?? 'local' };
  const detail = async () => {
    const response = await page.request.get(`${api}/projects/${projectId}`, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const savedTimeline = async () => {
    const response = await page.request.get(`${api}/timeline/${projectId}`, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return (await response.json()).timeline;
  };
  const before = await detail();
  const initial = await savedTimeline();
  const index = initial.video_segments.length - 1;
  const oldOut = initial.video_segments[index].source_out_ms;
  const shortenedOut = oldOut - 500;
  const oldRenderIds = before.renders.map((render: { id: string }) => render.id);
  expect(shortenedOut).toBeGreaterThan(initial.video_segments[index].source_in_ms);

  await page.goto(`/projects/${projectId}`);
  await page.getByRole('button', { name: 'Edit shots' }).click();
  const field = page.getByLabel(`Shot ${index + 1} out point`);
  await field.fill(String(shortenedOut / 1000));
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(field).toHaveValue(String(oldOut / 1000));
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  await expect(field).toHaveValue(String(shortenedOut / 1000));
  await page.getByRole('button', { name: 'Render edited version' }).click();

  await expect.poll(async () => {
    const current = await detail();
    if (current.prompt_version > before.prompt_version && current.jobs[0]?.state === 'failed') {
      throw new Error(current.jobs[0].error ?? 'real render failed');
    }
    return current.prompt_version > before.prompt_version && current.jobs[0]?.state === 'succeeded';
  }, { timeout: 8 * 60 * 1000, intervals: [2000, 5000] }).toBe(true);

  const after = await detail();
  const persisted = await savedTimeline();
  expect(after.renders.map((render: { id: string }) => render.id)).toEqual(expect.arrayContaining(oldRenderIds));
  expect(after.jobs[0].edit_options_snapshot.manual_timeline.shots[index].source_out_ms).toBe(shortenedOut);
  expect(persisted.video_segments[index].source_out_ms).toBe(shortenedOut);
  expect(persisted.total_duration_ms).toBe(initial.total_duration_ms - 500);

  const stale = await page.request.post(`${api}/projects/${projectId}/revisions`, {
    headers,
    data: {
      prompt: after.raw_prompt, target_platform: after.target_platform,
      edit_options: after.edit_options,
      idempotency_key: `stale-browser-check-${before.prompt_version}-${after.prompt_version}`,
      expected_prompt_version: before.prompt_version,
    },
  });
  expect(stale.status()).toBe(409);

  await page.reload();
  await page.getByRole('button', { name: 'Edit shots' }).click();
  await expect(page.getByLabel(`Shot ${index + 1} out point`)).toHaveValue(String(shortenedOut / 1000));
  const downloadEvent = page.waitForEvent('download');
  await page.locator('.render-card').getByRole('link', { name: 'Download MP4' }).click();
  const file = testInfo.outputPath('real-trim.mp4');
  await (await downloadEvent).saveAs(file);
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height',
    '-of', 'json', file,
  ], { encoding: 'utf8' }));
  expect(probe.streams.some((stream: { codec_type: string; width?: number; height?: number }) =>
    stream.codec_type === 'video' && stream.width === 1080 && stream.height === 1920)).toBe(true);
  expect(Math.abs(Number(probe.format.duration) - persisted.total_duration_ms / 1000)).toBeLessThan(0.2);
});
