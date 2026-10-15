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
  let projectId: string | null = null;
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
    projectId = page.url().match(/\/projects\/([^/]+)/)?.[1] ?? null;
    await page.locator('input[type=file]').setInputFiles({
      name: 'synthetic-travel.mp4',
      mimeType: 'video/mp4',
      buffer: readFileSync(source),
    });
    await expect(page.getByText('accepted', { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: 'Start processing' })).toBeEnabled();
    await page.getByRole('button', { name: 'Start processing' }).click();

    const renderCard = page.locator('.render-card');
    const preview = renderCard.locator('video.render-video');
    await expect(preview).toBeVisible({ timeout: 7 * 60 * 1000 });
    const downloadLink = renderCard.getByRole('link', { name: 'Download MP4' });
    await expect(downloadLink).toBeVisible();
    await preview.evaluate(async (element) => {
      const video = element as HTMLVideoElement;
      if (video.readyState < 1) await new Promise<void>((resolve, reject) => {
        video.addEventListener('loadedmetadata', () => resolve(), { once: true });
        video.addEventListener('error', () => reject(new Error('Browser could not read the rendered MP4.')), { once: true });
      });
    });
    expect(await preview.evaluate((element) => (element as HTMLVideoElement).videoWidth)).toBeGreaterThan(0);

    const downloadEvent = page.waitForEvent('download');
    await downloadLink.click();
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

    const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000/api/v1';
    const user = process.env.NEXT_PUBLIC_MONTA_USER ?? 'local';
    const headers = { 'X-MONTA-User': user };
    const getProject = async () => {
      const response = await page.request.get(`${apiBase}/projects/${projectId}`, { headers });
      expect(response.ok(), await response.text()).toBeTruthy();
      return response.json();
    };
    const beforeRevision = await getProject();
    const originalClipIds = beforeRevision.clips.map((clip: { id: string }) => clip.id).sort();
    const originalRenderIds = beforeRevision.renders.map((render: { id: string }) => render.id);
    const previousJobId = beforeRevision.jobs[0]?.id;
    expect(previousJobId).toBeTruthy();

    await page.getByRole('button', { name: 'Revise edit' }).click();
    const revisionForm = page.locator('form.edit-options');
    const revisedPrompt = 'Make the ending happier';
    await revisionForm.getByLabel('Editing prompt').fill(revisedPrompt);
    await revisionForm.getByRole('button', { name: 'Create new version' }).click();
    await expect.poll(async () => {
      const detail = await getProject();
      const job = detail.jobs[0];
      return job?.id !== previousJobId && ['succeeded', 'failed'].includes(job?.state) ? job.state : 'processing';
    }, { timeout: 7 * 60 * 1000, intervals: [1000, 2000, 5000] }).toBe('succeeded');

    const afterRevision = await getProject();
    if (afterRevision.jobs[0]?.revision_mode == null) {
      console.log('Revision result diagnostic:', JSON.stringify({
        job: afterRevision.jobs[0],
        projectStatus: afterRevision.status,
        projectStatusDetail: afterRevision.status_detail,
      }));
    }
    expect(afterRevision.id).toBe(beforeRevision.id);
    expect(afterRevision.prompt_version).toBe(beforeRevision.prompt_version + 1);
    expect(afterRevision.raw_prompt).toBe(revisedPrompt);
    expect(afterRevision.clips.map((clip: { id: string }) => clip.id).sort()).toEqual(originalClipIds);
    expect(afterRevision.renders.map((render: { id: string }) => render.id)).toEqual(expect.arrayContaining(originalRenderIds));
    expect(afterRevision.jobs[0].prompt_snapshot).toBe(revisedPrompt);
    expect(afterRevision.jobs[0].revision_mode).toBe('incremental');
    expect(afterRevision.jobs[0].inherited_requirements).toEqual(expect.arrayContaining(['genre', 'color_grade']));
    expect(afterRevision.renders.find((render: { is_current: boolean }) => render.is_current)?.job_id)
      .toBe(afterRevision.jobs[0].id);

    await page.reload();
    await expect(page.getByText(revisedPrompt, { exact: true }).first()).toBeVisible();
    const revisedPreview = page.locator('.render-card video.render-video');
    await expect(revisedPreview).toBeVisible();
    await revisedPreview.evaluate(async (element) => {
      const video = element as HTMLVideoElement;
      if (video.readyState < 1) await new Promise<void>((resolve, reject) => {
        video.addEventListener('loadedmetadata', () => resolve(), { once: true });
        video.addEventListener('error', () => reject(new Error('Browser could not read the revised MP4.')), { once: true });
      });
    });
    expect(await revisedPreview.evaluate((element) => (element as HTMLVideoElement).videoWidth)).toBeGreaterThan(0);
    await revisedPreview.evaluate(async (element) => {
      const video = element as HTMLVideoElement;
      video.muted = true;
      const playing = new Promise<void>((resolve) => video.addEventListener('playing', () => resolve(), { once: true }));
      await video.play();
      await playing;
      await new Promise((resolve) => setTimeout(resolve, 250));
      video.pause();
    });
    const revisedDownloadEvent = page.waitForEvent('download');
    await page.locator('.render-card').getByRole('link', { name: 'Download MP4' }).click();
    const revisedDownload = await revisedDownloadEvent;
    const revisedFile = testInfo.outputPath('monta-live-revision.mp4');
    await revisedDownload.saveAs(revisedFile);
    const revisedProbe = JSON.parse(execFileSync('ffprobe', [
      '-v', 'error', '-show_entries', 'stream=codec_type,width,height', '-show_entries', 'format=duration',
      '-of', 'json', revisedFile,
    ], { encoding: 'utf8' }));
    const revisedVideo = revisedProbe.streams.find((stream: { codec_type: string }) => stream.codec_type === 'video');
    expect(revisedVideo).toBeTruthy();
    expect(Math.abs(Number(revisedProbe.format.duration) - 6)).toBeLessThanOrEqual(0.5);
  } finally {
    if (projectId) {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000/api/v1';
      await page.request.delete(`${apiBase}/projects/${projectId}`, {
        headers: { 'X-MONTA-User': process.env.NEXT_PUBLIC_MONTA_USER ?? 'local' },
      }).catch(() => undefined);
    }
    rmSync(fixtureDir, { recursive: true, force: true });
  }
});
