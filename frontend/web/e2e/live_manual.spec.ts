import { execFileSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

const projectId = process.env.MONTA_LIVE_MANUAL_PROJECT_ID;
test.skip(!projectId, 'Set MONTA_LIVE_MANUAL_PROJECT_ID to a completed local project.');

test('manual split and trim persist as a new version and render a playable MP4', async ({ page }, testInfo) => {
  test.setTimeout(9 * 60 * 1000);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000/api/v1';
  const headers = { 'X-MONTA-User': process.env.NEXT_PUBLIC_MONTA_USER ?? 'local' };
  const detail = async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const before = await detail();
  const previousRenderIds = before.renders.map((render: { id: string }) => render.id);
  const previousClipIds = before.clips.map((clip: { id: string }) => clip.id);
  await page.goto(`/projects/${projectId}`);
  await page.getByRole('button', { name: 'Edit shots' }).click();
  await page.getByRole('button', { name: 'Split' }).last().click();
  await expect(page.getByLabel('Shot 6 in point')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByLabel('Shot 6 in point')).toHaveCount(0);
  await page.getByRole('button', { name: 'Redo' }).click();
  await page.getByLabel('Shot 5 out point').fill('6.1');
  await page.getByLabel('Shot 6 in point').fill('6.1');
  await page.getByRole('button', { name: 'Render edited version' }).click();
  await expect.poll(async () => {
    const current = await detail();
    return current.prompt_version > before.prompt_version && ['succeeded', 'failed'].includes(current.jobs[0]?.state)
      ? current.jobs[0].state : 'processing';
  }, { timeout: 8 * 60 * 1000, intervals: [2000, 5000] }).toBe('succeeded');
  const after = await detail();
  expect(after.prompt_version).toBe(before.prompt_version + 1);
  expect(after.clips.map((clip: { id: string }) => clip.id)).toEqual(previousClipIds);
  expect(after.renders.map((render: { id: string }) => render.id)).toEqual(expect.arrayContaining(previousRenderIds));
  expect(after.jobs[0].edit_options_snapshot.manual_timeline.shots).toHaveLength(6);
  expect(after.jobs[0].edit_options_snapshot.manual_timeline.shots[4].source_out_ms).toBe(6100);
  expect(after.jobs[0].edit_options_snapshot.manual_timeline.shots[5].source_in_ms).toBe(6100);
  await page.reload();
  const preview = page.locator('.render-card video.render-video');
  await expect(preview).toBeVisible();
  await preview.evaluate(async (element) => {
    const video = element as HTMLVideoElement;
    if (video.readyState < 1) await new Promise<void>((resolve, reject) => {
      video.addEventListener('loadedmetadata', () => resolve(), { once: true });
      video.addEventListener('error', () => reject(new Error('Browser could not load the MP4')), { once: true });
    });
  });
  expect(await preview.evaluate((element) => (element as HTMLVideoElement).videoWidth)).toBe(1080);
  const downloadEvent = page.waitForEvent('download');
  await page.locator('.render-card').getByRole('link', { name: 'Download MP4' }).click();
  const download = await downloadEvent;
  const file = testInfo.outputPath('manual-edit.mp4');
  await download.saveAs(file);
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_name,width,height', '-of', 'json', file,
  ], { encoding: 'utf8' }));
  expect(probe.streams.some((stream: { codec_name: string; width?: number; height?: number }) =>
    stream.codec_name === 'h264' && stream.width === 1080 && stream.height === 1920)).toBe(true);
  expect(Math.abs(Number(probe.format.duration) - 22.5)).toBeLessThan(0.5);
});

test('saved manual edit reopens and downloads through the real API', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000/api/v1';
  const headers = { 'X-MONTA-User': process.env.NEXT_PUBLIC_MONTA_USER ?? 'local' };
  const response = await page.request.get(`${apiBase}/projects/${projectId}`, { headers });
  expect(response.ok()).toBeTruthy();
  const project = await response.json();
  expect(project.jobs[0].state).toBe('succeeded');
  expect(project.jobs[0].edit_options_snapshot.manual_timeline.shots).toHaveLength(6);
  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole('heading', { name: 'Manual timeline' })).toBeVisible();
  await expect(page.locator('.render-card video.render-video')).toBeVisible();
  await expect(page.getByText('Manual choice').first()).toBeVisible();
  await expect(page.getByLabel('A version preview')).toBeVisible();
  await expect(page.getByLabel('B version preview')).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.locator('.render-card').getByRole('link', { name: 'Download MP4' }).click();
  const download = await downloadEvent;
  const file = testInfo.outputPath('saved-manual-edit.mp4');
  await download.saveAs(file);
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_name,width,height', '-of', 'json', file,
  ], { encoding: 'utf8' }));
  expect(probe.streams.some((stream: { codec_name: string; width?: number; height?: number }) =>
    stream.codec_name === 'h264' && stream.width === 1080 && stream.height === 1920)).toBe(true);
  expect(Math.abs(Number(probe.format.duration) - 22.5)).toBeLessThan(0.5);
});
