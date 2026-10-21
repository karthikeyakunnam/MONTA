import { expect, test } from '@playwright/test';

const projectId = process.env.MONTA_LIVE_REVIEW_PROJECT_ID;
test.skip(!projectId, 'Set MONTA_LIVE_REVIEW_PROJECT_ID to a project with a held candidate.');

test('warning candidate stays separate from the current render and offers explicit promotion', async ({ page }) => {
  const response = await page.request.get(`http://127.0.0.1:8000/api/v1/projects/${projectId}`);
  expect(response.ok()).toBeTruthy();
  const project = await response.json();
  const candidate = project.renders.find((item: {
    requires_review: boolean; promotion_available: boolean;
  }) => item.requires_review);
  expect(candidate).toBeTruthy();
  expect(candidate.promotion_available).toBeTruthy();
  expect(project.render_url).not.toContain(candidate.id);

  await page.goto(`/projects/${projectId}`);
  await expect(page.getByText('Candidate · review before promotion')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Promote after review' })).toBeVisible();
});
