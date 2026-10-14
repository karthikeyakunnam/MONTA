import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';

const live = process.env.MONTA_LIVE_E2E === 'true';

test('real browser workflow creates, uploads, processes, previews and downloads an MP4', async ({ page }, testInfo) => {
  test.skip(!live, 'Set MONTA_LIVE_E2E=true after starting the API, Redis, PostgreSQL, Ollama and worker.');
  test.setTimeout(8 * 60 * 1000);

  const fixtureDir = mkdtempSync(join(tmpdir(), 'monta-live-e2e-'));
  const source = join(fixtureDir, 'synthetic-travel.mp4');
  const title = `Live workflow ${Date.now()}`;
  try {
    execFileSync('ffmpeg', [
      '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=1280x720:rate=24:duration=8',
      '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8', '-c:v', 'libx264', '-preset', 'ultrafast',
      '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', source,
    ]);

    await page.goto('/');
    await expect(page.getByText(/API healthy|unavailable/).first()).toBeVisible({ timeout: 20_000 });
    await page.getByLabel('Project name').fill(title);
    await page.getByLabel('What should MONTA make?').fill(
      'Make a concise travel montage with a clear beginning and ending, natural colors and a calm mood.',
    );
    await page.getByLabel('Target platform').selectOption('youtube');
    await page.getByText('Fine tune the edit').click();
    await page.locator('input[type=number]').fill('6');
    await page.getByLabel('Captions').selectOption('None');
    await page.getByLabel('Add generated background music').uncheck();
    await page.getByRole('button', { name: 'Create project' }).click();

    await expect(page.getByRole('heading', { name: title })).toBeVisible();
    await page.locator('input[type=file]').setInputFiles({
      name: 'synthetic-travel.mp4',
      mimeType: 'video/mp4',
      buffer: readFileSync(source),
    });
    await expect(page.getByText('accepted', { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: 'Start processing' })).toBeEnabled();
    await page.getByRole('button', { name: 'Start processing' }).click();

    const preview = page.locator('video.render-video');
    await expect(preview).toBeVisible({ timeout: 7 * 60 * 1000 });
    await expect(page.getByRole('link', { name: 'Download MP4' })).toBeVisible();
    await preview.evaluate(async (element) => {
      const video = element as HTMLVideoElement;
      if (video.readyState < 1) await new Promise<void>((resolve, reject) => {
        video.addEventListener('loadedmetadata', () => resolve(), { once: true });
        video.addEventListener('error', () => reject(new Error('Browser could not read the rendered MP4.')), { once: true });
      });
    });
    expect(await preview.evaluate((element) => (element as HTMLVideoElement).videoWidth)).toBeGreaterThan(0);

    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('link', { name: 'Download MP4' }).click();
    const download = await downloadEvent;
    const downloadedFile = testInfo.outputPath('monta-live-render.mp4');
    await download.saveAs(downloadedFile);
    const probe = JSON.parse(execFileSync('ffprobe', [
      '-v', 'error', '-show_entries', 'stream=codec_type,width,height', '-show_entries', 'format=duration',
      '-of', 'json', downloadedFile,
    ], { encoding: 'utf8' }));
    const videoStream = probe.streams.find((stream: { codec_type: string }) => stream.codec_type === 'video');
    expect(videoStream).toBeTruthy();
    expect(videoStream.width).toBeGreaterThan(0);
    expect(videoStream.height).toBeGreaterThan(0);
    expect(Number(probe.format.duration)).toBeGreaterThan(0);
  } finally {
    rmSync(fixtureDir, { recursive: true, force: true });
  }
});
