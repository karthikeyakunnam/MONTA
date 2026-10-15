import { execFileSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

const projectId = process.env.MONTA_LIVE_REVISION_PROJECT_ID;
test.skip(!projectId, 'Set MONTA_LIVE_REVISION_PROJECT_ID to a completed project with uploaded media and an accessible prior render.');

test('real browser revises an existing project and keeps both validated renders', async ({ page }, testInfo) => {
  test.setTimeout(8 * 60 * 1000);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000/api/v1';
  const user = process.env.NEXT_PUBLIC_MONTA_USER ?? 'local';
  const headers = { 'X-MONTA-User': user };
  const getProject = async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };

  const before = await getProject();
  expect(before.id).toBe(projectId);
  expect(before.clips.length).toBeGreaterThan(0);
  expect(before.render_url).toBeTruthy();
  expect(before.renders.length).toBeGreaterThan(0);
  const originalClipIds = before.clips.map((clip: { id: string }) => clip.id).sort();
  const originalRenderIds = before.renders.map((render: { id: string }) => render.id);
  const previousJobId = before.jobs[0]?.id;
  expect(previousJobId).toBeTruthy();

  const prompt = 'Keep this calm travel montage and natural colors, but make the final shot feel more hopeful.';
  await page.goto(`/projects/${projectId}`);
  await expect(page.getByRole('heading', { name: before.title })).toBeVisible();
  await page.getByRole('button', { name: 'Revise edit' }).click();
  const form = page.locator('form.edit-options');
  await form.getByLabel('Editing prompt').fill(prompt);
  await form.getByLabel('Export target').selectOption('youtube');
  await form.getByLabel('Target duration (seconds)').fill('6');
  await form.getByLabel('Aspect ratio').selectOption('16:9');
  await form.getByLabel('Color style').selectOption('warm');
  await form.getByLabel('Captions').selectOption('None');
  await form.locator('input[type="checkbox"]').uncheck();
  await form.getByRole('button', { name: 'Create new version' }).click();

  await expect.poll(async () => {
    const detail = await getProject();
    return detail.jobs[0]?.id !== previousJobId && ['succeeded', 'failed'].includes(detail.jobs[0]?.state)
      ? detail.jobs[0].state : 'processing';
  }, { timeout: 7 * 60 * 1000, intervals: [1000, 2000, 5000] }).toBe('succeeded');

  const after = await getProject();
  expect(after.id).toBe(before.id);
  expect(after.prompt_version).toBe(before.prompt_version + 1);
  expect(after.raw_prompt).toBe(prompt);
  expect(after.clips.map((clip: { id: string }) => clip.id).sort()).toEqual(originalClipIds);
  expect(after.renders.map((render: { id: string }) => render.id)).toEqual(expect.arrayContaining(originalRenderIds));
  expect(after.renders.some((render: { job_id: string | null }) => render.job_id === after.jobs[0].id)).toBe(true);
  expect(after.jobs[0].prompt_snapshot).toBe(prompt);
  expect(after.jobs[0].edit_options_snapshot).toMatchObject({
    target_duration_s: 6, aspect_ratio: '16:9', color_grade: 'warm', caption_style: 'None', add_music: false,
  });
  expect(after.jobs[1].id).toBe(previousJobId);

  await page.reload();
  await expect(page.getByText(prompt, { exact: true }).first()).toBeVisible();
  const renderCard = page.locator('.render-card');
  const preview = renderCard.locator('video.render-video');
  await expect(preview).toBeVisible();
  await preview.evaluate(async (element) => {
    const video = element as HTMLVideoElement;
    if (video.readyState < 1) await new Promise<void>((resolve, reject) => {
      video.addEventListener('loadedmetadata', () => resolve(), { once: true });
      video.addEventListener('error', () => reject(new Error('Browser could not read the revised MP4.')), { once: true });
    });
  });
  expect(await preview.evaluate((element) => (element as HTMLVideoElement).videoWidth)).toBeGreaterThan(0);

  const downloadEvent = page.waitForEvent('download');
  await renderCard.getByRole('link', { name: 'Download MP4' }).click();
  const download = await downloadEvent;
  const downloadedFile = testInfo.outputPath('monta-live-revision.mp4');
  await download.saveAs(downloadedFile);
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height',
    '-show_entries', 'format=duration', '-of', 'json', downloadedFile,
  ], { encoding: 'utf8' }));
  const video = probe.streams.find((stream: { codec_type: string }) => stream.codec_type === 'video');
  expect(video).toBeTruthy();
  expect(video.width).toBe(1920);
  expect(video.height).toBe(1080);
  expect(Math.abs(Number(probe.format.duration) - 6)).toBeLessThanOrEqual(0.5);
});
