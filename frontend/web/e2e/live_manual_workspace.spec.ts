import { expect, test } from '@playwright/test';

const projectId = process.env.MONTA_LIVE_MANUAL_PROJECT_ID;
test.skip(!projectId, 'Set MONTA_LIVE_MANUAL_PROJECT_ID to a completed local project.');

test('visual edits save to the real project and reopen after reload', async ({ page }, testInfo) => {
  const apiProject = `http://127.0.0.1:8000/api/v1/projects/${projectId}`;
  const beforeResponse = await page.request.get(apiProject);
  expect(beforeResponse.ok()).toBeTruthy();
  const before = await beforeResponse.json();
  let saved = false;
  try {
  await page.goto(`/projects/${projectId}`);
  await page.getByRole('button', { name: 'Edit shots' }).click();
  const clips = page.locator('.manual-track-clip');
  const count = await clips.count();
  expect(count).toBeGreaterThan(1);
  await page.locator('.manual-workspace').screenshot({ path: testInfo.outputPath('manual-workspace.png') });
  const first = {
    source: await page.getByLabel('Shot 1 source clip').inputValue(),
    in: await page.getByLabel('Shot 1 in point').inputValue(),
    out: await page.getByLabel('Shot 1 out point').inputValue(),
  };
  const second = {
    source: await page.getByLabel('Shot 2 source clip').inputValue(),
    in: await page.getByLabel('Shot 2 in point').inputValue(),
    out: await page.getByLabel('Shot 2 out point').inputValue(),
  };
  await clips.nth(1).dragTo(clips.first());
  await expect(page.getByLabel('Shot 1 source clip')).toHaveValue(second.source);
  await expect(page.getByLabel('Shot 1 in point')).toHaveValue(second.in);
  await expect(page.getByLabel('Shot 1 out point')).toHaveValue(second.out);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByLabel('Shot 1 source clip')).toHaveValue(first.source);
  await expect(page.getByLabel('Shot 1 in point')).toHaveValue(first.in);
  await expect(page.getByLabel('Shot 1 out point')).toHaveValue(first.out);
  const handle = page.getByLabel('Shot 1 in handle');
  const before = Number(await handle.inputValue());
  const out = Number(first.out);
  if (out - before > 0.2) {
    await handle.focus();
    await handle.press('ArrowRight');
    await expect(page.getByLabel('Shot 1 in point')).not.toHaveValue(first.in);
    await page.getByRole('button', { name: 'Undo' }).click();
    await expect(page.getByLabel('Shot 1 in point')).toHaveValue(first.in);
  }
  const title = page.getByLabel('Title 1 text');
  expect(await title.count()).toBe(1);
  await page.getByText('Titles and credits · edit text, timing and placement').click();
  const original = await title.inputValue();
  await title.fill(`${original} revised`);
  await expect(page.locator('.manual-track-text').first()).toContainText(`${original} revised`);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(title).toHaveValue(original);
  const revised = `${original} revised`;
  await title.fill(revised);
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved to project' })).toBeVisible();
  saved = true;
  const savedResponse = await page.request.get(apiProject);
  expect(savedResponse.ok()).toBeTruthy();
  const stored = await savedResponse.json();
  expect(stored.manual_draft.titles[0].text).toBe(revised);
  await page.reload();
  await page.getByRole('button', { name: 'Edit shots' }).click();
  await page.getByText('Titles and credits · edit text, timing and placement').click();
  await expect(page.getByLabel('Title 1 text')).toHaveValue(revised);
  await page.getByRole('button', { name: 'Revise edit' }).click();
  await expect(page.getByLabel('Resolve manual timeline')).toBeVisible();
  } finally {
    if (saved) {
      const currentResponse = await page.request.get(apiProject);
      expect(currentResponse.ok()).toBeTruthy();
      const current = await currentResponse.json();
      const restore = await page.request.put(`${apiProject}/manual-draft`, { data: {
        timeline: before.manual_draft,
        expected_prompt_version: current.prompt_version,
        expected_draft_revision: current.manual_draft_revision,
      } });
      expect(restore.ok()).toBeTruthy();
    }
  }
});
