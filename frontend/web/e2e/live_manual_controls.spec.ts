import { execFileSync } from 'node:child_process';
import { expect, test } from '@playwright/test';

const projectId = process.env.MONTA_LIVE_MANUAL_CONTROLS_PROJECT_ID;
test.skip(!projectId, 'Set MONTA_LIVE_MANUAL_CONTROLS_PROJECT_ID to a completed local project.');

test('manual grade, audio and transition controls reach a new real render', async ({ page }, testInfo) => {
  test.setTimeout(9 * 60 * 1000);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000/api/v1';
  const headers = { 'X-MONTA-User': process.env.NEXT_PUBLIC_MONTA_USER ?? 'local' };
  const getProject = async () => {
    const response = await page.request.get(`${apiBase}/projects/${projectId}`, { headers });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const before = await getProject();
  const oldRenderIds = before.renders.map((render: { id: string }) => render.id);
  await page.goto(`/projects/${projectId}`);
  await page.getByRole('button', { name: 'Edit shots' }).click();
  await page.getByText('Shot controls').first().click();
  await page.getByLabel('Shot 1 brightness').fill('0.1');
  await page.getByLabel('Shot 1 fade in').fill('100');
  await page.getByLabel('Shot 2 transition', { exact: true }).selectOption('crossfade');
  await page.getByText('Shot controls').nth(1).click();
  await page.getByLabel('Shot 2 transition duration').fill('200');
  await page.getByLabel('Music volume').fill('0.3');
  await page.getByLabel('Speech ducking').selectOption('off');
  await page.getByRole('button', { name: 'Render edited version' }).click();
  await expect.poll(async () => {
    const current = await getProject();
    if (current.prompt_version > before.prompt_version && current.jobs[0]?.state === 'failed') {
      throw new Error(current.jobs[0].error ?? 'Manual render failed');
    }
    return current.prompt_version > before.prompt_version && current.jobs[0]?.state === 'succeeded';
  }, { timeout: 8 * 60 * 1000, intervals: [2000, 5000] }).toBe(true);
  const after = await getProject();
  expect(after.renders.map((render: { id: string }) => render.id)).toEqual(expect.arrayContaining(oldRenderIds));
  const manual = after.jobs[0].edit_options_snapshot.manual_timeline;
  expect(manual.shots[0].color.brightness).toBe(0.1);
  expect(manual.shots[0].fade_in_ms).toBe(100);
  expect(manual.shots[1].transition_in).toBe('crossfade');
  expect(manual.shots[1].transition_in_ms).toBe(200);
  expect(manual.music_volume).toBe(0.3);
  expect(manual.music_ducking).toBe(false);
  const timelineResponse = await page.request.get(`${apiBase}/timeline/${projectId}`, { headers });
  expect(timelineResponse.ok()).toBeTruthy();
  const timeline = (await timelineResponse.json()).timeline;
  expect(timeline.video_segments[0].color.brightness).toBe(0.1);
  expect(timeline.video_segments[0].fade_in_ms).toBe(100);
  expect(timeline.video_segments[1].transition_in_ms).toBe(200);
  expect(timeline.audio_segments.every((track: { volume: number; ducking: boolean }) =>
    track.volume === 0.3 && track.ducking === false)).toBe(true);
  await page.reload();
  const preview = page.locator('.render-card video.render-video');
  await expect(preview).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.locator('.render-card').getByRole('link', { name: 'Download MP4' }).click();
  const download = await downloadEvent;
  const file = testInfo.outputPath('manual-controls.mp4');
  await download.saveAs(file);
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_name,width,height', '-of', 'json', file,
  ], { encoding: 'utf8' }));
  expect(probe.streams.some((stream: { codec_name: string; width?: number; height?: number }) =>
    stream.codec_name === 'h264' && stream.width === 1080 && stream.height === 1920)).toBe(true);
  expect(Math.abs(Number(probe.format.duration) - 22.3)).toBeLessThan(0.5);
});

test('saved manual controls reopen with their real compiled render', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000/api/v1';
  const headers = { 'X-MONTA-User': process.env.NEXT_PUBLIC_MONTA_USER ?? 'local' };
  const projectResponse = await page.request.get(`${apiBase}/projects/${projectId}`, { headers });
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();
  expect(project.jobs[0].state).toBe('succeeded');
  const manual = project.jobs[0].edit_options_snapshot.manual_timeline;
  expect(manual.shots[0].color.brightness).toBe(0.1);
  expect(manual.shots[0].fade_in_ms).toBe(100);
  expect(manual.shots[1].transition_in_ms).toBe(200);
  expect(manual.music_volume).toBe(0.3);
  expect(manual.music_ducking).toBe(false);
  const timelineResponse = await page.request.get(`${apiBase}/timeline/${projectId}`, { headers });
  expect(timelineResponse.ok()).toBeTruthy();
  const saved = await timelineResponse.json();
  expect(saved.timeline.video_segments[0].color.brightness).toBe(0.1);
  expect(saved.timeline.video_segments[1].transition_in_ms).toBe(200);
  expect(saved.timeline.audio_segments.every((track: { volume: number; ducking: boolean }) =>
    track.volume === 0.3 && track.ducking === false)).toBe(true);
  const graph = saved.trace.compiled_execution_plan?.filter_graph ?? '';
  expect(graph).toContain('eq=brightness=0.100');
  expect(graph).toContain('xfade=transition=fade:duration=0.200');
  expect(graph).toContain('concat=n=2:v=1:a=1');
  expect(graph).toContain('volume=0.300');
  await page.goto(`/projects/${projectId}`);
  await expect(page.locator('.render-card video.render-video')).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.locator('.render-card').getByRole('link', { name: 'Download MP4' }).click();
  const file = testInfo.outputPath('saved-manual-controls.mp4');
  await (await downloadEvent).saveAs(file);
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_name,width,height,duration', '-of', 'json', file,
  ], { encoding: 'utf8' }));
  const video = probe.streams.find((stream: { codec_name: string }) => stream.codec_name === 'h264');
  const audio = probe.streams.find((stream: { codec_name: string }) => stream.codec_name === 'aac');
  expect(video?.width).toBe(1080);
  expect(video?.height).toBe(1920);
  expect(Math.abs(Number(video?.duration) - 22.3)).toBeLessThan(0.5);
  expect(Math.abs(Number(audio?.duration) - Number(video?.duration))).toBeLessThan(0.15);
});
