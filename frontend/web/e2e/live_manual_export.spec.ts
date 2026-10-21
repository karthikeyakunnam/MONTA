import { expect, test } from '@playwright/test';

const projectId = process.env.MONTA_LIVE_EXPORT_PROJECT_ID;
test.skip(!projectId, 'Set MONTA_LIVE_EXPORT_PROJECT_ID to an isolated local acceptance project.');

test('browser trim is saved, reopened, then submitted to the real render worker', async ({ page }) => {
  const url = `http://127.0.0.1:8000/api/v1/projects/${projectId}`;
  const beforeResponse = await page.request.get(url);
  expect(beforeResponse.ok()).toBeTruthy();
  const before = await beforeResponse.json();
  expect(before.title).toContain('MONTA acceptance isolated');
  const previousVersion = before.prompt_version;

  await page.goto(`/projects/${projectId}`);
  await page.getByRole('button', { name: 'Edit shots' }).click();
  const inPoint = page.getByLabel('Shot 1 in point');
  const trimmed = '0.2';
  await inPoint.fill(trimmed);
  await expect(inPoint).toHaveValue(trimmed);
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved to project' })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: 'Edit shots' }).click();
  await expect(page.getByLabel('Shot 1 in point')).toHaveValue(trimmed);
  await page.getByRole('button', { name: 'Render edited version' }).click();
  await expect.poll(async () => {
    const response = await page.request.get(url);
    const project = await response.json();
    return project.prompt_version;
  }, { timeout: 20000 }).toBe(previousVersion + 1);

  const submitted = await (await page.request.get(url)).json();
  expect(submitted.jobs[0].edit_options_snapshot.manual_timeline.shots[0].source_in_ms)
    .toBe(Math.round(Number(trimmed) * 1000));
});
