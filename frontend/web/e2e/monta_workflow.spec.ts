import { test, expect } from '@playwright/test';

test.describe('MONTA Video Creation Workflow', () => {
  test('1. Validates project creation form and prompt validation', async ({ page }) => {
    // Intercept project listing
    await page.route('**/api/v1/projects', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto('/');

    // Verify page header
    await expect(page.locator('h1.page-title')).toHaveText('New project');

    const titleInput = page.locator('input[placeholder="Bali trip, June"]');
    const promptInput = page.locator('textarea');
    const submitBtn = page.locator('button[type="submit"]');

    // Submit button should be disabled when empty
    await expect(submitBtn).toBeDisabled();

    // Fill title only, still disabled
    await titleInput.fill('Sunset Surf Reel');
    await expect(submitBtn).toBeDisabled();

    // Fill prompt with < 3 chars, still disabled
    await promptInput.fill('Hi');
    await expect(submitBtn).toBeDisabled();

    // Fill prompt with >= 3 chars, should be enabled
    await promptInput.fill('High-energy fast-paced beach montage with hype music');
    await expect(submitBtn).toBeEnabled();

    // Select target platform
    const platformSelect = page.locator('select');
    await platformSelect.selectOption('tiktok');
    await expect(platformSelect).toHaveValue('tiktok');
  });

  test('2. Submits new project and navigates to project dashboard', async ({ page }) => {
    const mockProjectId = 'proj_test_workflow_01';

    await page.route('**/api/v1/projects', async (route) => {
      if (route.request().method() === 'POST') {
        const payload = JSON.parse(route.request().postData() || '{}');
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            id: mockProjectId,
            title: payload.title,
            raw_prompt: payload.prompt,
            target_platform: payload.target_platform,
            status: 'draft',
            created_at: new Date().toISOString(),
          }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
    });

    await page.route(`**/api/v1/projects/${mockProjectId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: mockProjectId,
          title: 'Sunset Surf Reel',
          raw_prompt: 'High-energy fast-paced beach montage with hype music',
          target_platform: 'tiktok',
          status: 'draft',
          status_detail: 'upload footage to begin',
          clips: [],
          jobs: [],
          quota: [],
          clip_count: 0,
          total_duration_s: 0,
          story_pattern: null,
          story_duration_s: null,
          story_cuts: null,
          story_score: null,
          render_status: null,
          render_url: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
    });

    await page.route(`**/api/v1/projects/${mockProjectId}/events`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.route('**/api/v1/upload/limits', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          allowed_extensions: ['.mp4', '.mov'],
          max_file_bytes: 500000000,
          max_request_bytes: 1000000000,
          max_files_per_request: 20,
          max_clip_seconds: 600,
          min_clip_seconds: 1,
          resolution_classes: [],
        }),
      });
    });

    await page.goto('/');
    await page.locator('input[placeholder="Bali trip, June"]').fill('Sunset Surf Reel');
    await page.locator('textarea').fill('High-energy fast-paced beach montage with hype music');
    await page.locator('button[type="submit"]').click();

    // Verifies navigation to project dashboard
    await expect(page).toHaveURL(new RegExp(`/projects/${mockProjectId}`));
    await expect(page.locator('h1.page-title')).toContainText('Sunset Surf Reel');
  });

  test('3. Handles project creation failure with error banner and retry', async ({ page }) => {
    let callCount = 0;

    await page.route('**/api/v1/projects', async (route) => {
      if (route.request().method() === 'POST') {
        callCount++;
        if (callCount === 1) {
          await route.fulfill({
            status: 500,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Database connection timeout during project creation' }),
          });
        } else {
          await route.fulfill({
            status: 201,
            contentType: 'application/json',
            body: JSON.stringify({
              id: 'proj_retry_ok',
              title: 'Retry Title',
              raw_prompt: 'Prompt text',
              target_platform: 'instagram',
              status: 'draft',
              created_at: new Date().toISOString(),
            }),
          });
        }
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
    });

    await page.route('**/api/v1/projects/proj_retry_ok**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'proj_retry_ok',
          title: 'Retry Title',
          status: 'draft',
          clips: [],
          jobs: [],
          quota: [],
          clip_count: 0,
          total_duration_s: 0,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
    });

    await page.goto('/');
    await page.locator('input[placeholder="Bali trip, June"]').fill('Retry Title');
    await page.locator('textarea').fill('Prompt text description');
    await page.locator('button[type="submit"]').click();

    // Verify error banner is displayed
    const errorBanner = page.locator('.banner.error');
    await expect(errorBanner).toBeVisible();
    await expect(errorBanner).toContainText('Creating the project failed on the server');

    // Click retry on banner
    const retryBtn = errorBanner.locator('button:has-text("Try again")');
    await expect(retryBtn).toBeVisible();
    await retryBtn.click();
    await expect(page).toHaveURL(/proj_retry_ok/);
  });

  test('4. Full pipeline completion, live progress tracking and export download', async ({ page }) => {
    const projectId = 'proj_render_complete_01';
    let pipelineRunning = false;

    await page.route(`**/api/v1/projects/${projectId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: projectId,
          title: 'Cinematic Reel',
          description: null,
          status: pipelineRunning ? 'complete' : 'draft',
          status_detail: pipelineRunning ? 'video ready: 10.5s, 1080x1920' : 'ready to process',
          raw_prompt: 'Cinematic action montage',
          target_platform: 'tiktok',
          clip_count: 1,
          total_duration_s: 10.5,
          story_pattern: pipelineRunning ? 'three_act_journey' : null,
          story_duration_s: pipelineRunning ? 10.5 : null,
          story_cuts: pipelineRunning ? 2 : null,
          story_score: pipelineRunning ? 8.8 : null,
          render_status: pipelineRunning ? 'complete' : null,
          render_url: pipelineRunning ? `/api/v1/render/render_final_123/download` : null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          clips: [
            {
              id: 'clip_01',
              status: 'analyzed',
              filename: 'beach_surf.mp4',
              original_filename: 'beach_surf.mp4',
              format: 'mp4',
              duration: 10.5,
              fps: 30.0,
              resolution: '1080x1920',
              resolution_class: '1080p',
              video_codec: 'h264',
              bitrate: 8000000,
              file_size_bytes: 15000000,
              has_audio: true,
              audio_streams: null,
              sha256: 'abc123456789',
              duplicate_of: null,
              rejection_issues: null,
              created_at: new Date().toISOString(),
            },
          ],
          jobs: pipelineRunning
            ? [
                {
                  id: 'job_render_999',
                  kind: 'pipeline',
                  state: 'succeeded',
                  stage: 'complete',
                  task_id: 'task_001',
                  attempts: 1,
                  error: null,
                  created_at: new Date().toISOString(),
                  started_at: new Date().toISOString(),
                  finished_at: new Date().toISOString(),
                },
              ]
            : [],
          quota: [],
        }),
      });
    });

    await page.route(`**/api/v1/projects/${projectId}/pipeline`, async (route) => {
      pipelineRunning = true;
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job_render_999',
          tracking_id: 'job_render_999',
          task_id: 'task_001',
          state: 'queued',
          project_status: 'queued',
        }),
      });
    });

    await page.route(`**/api/v1/projects/${projectId}/events`, async (route) => {
      const events = pipelineRunning
        ? [
            { project_id: projectId, stage: 'analyzing', message: 'Analyzed 1 of 1 clips', at: '2026-10-14T07:00:00Z' },
            { project_id: projectId, stage: 'story_building', message: 'Building the story', at: '2026-10-14T07:00:02Z' },
            { project_id: projectId, stage: 'timeline_building', message: 'Composing audio score', at: '2026-10-14T07:00:04Z' },
            { project_id: projectId, stage: 'rendering', message: 'Rendering video: 100%', at: '2026-10-14T07:00:06Z' },
            { project_id: projectId, stage: 'complete', message: 'video ready: 10.5s, 1080x1920', at: '2026-10-14T07:00:08Z' },
          ]
        : [];
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(events) });
    });

    await page.route('**/api/v1/upload/limits', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          allowed_extensions: ['.mp4'],
          max_file_bytes: 500000000,
          max_request_bytes: 1000000000,
          max_files_per_request: 20,
          max_clip_seconds: 600,
          min_clip_seconds: 1,
          resolution_classes: [],
        }),
      });
    });

    await page.goto(`/projects/${projectId}`);

    // Verify clip is listed in ClipTable
    await expect(page.locator('text=beach_surf.mp4')).toBeVisible();

    // Start processing
    const runBtn = page.locator('button:has-text("Start processing")');
    await expect(runBtn).toBeVisible();
    await runBtn.click();

    // Verify completion status and download link
    const downloadLink = page.locator('a[href*="/api/v1/render/"]');
    await expect(downloadLink).toBeVisible();
    await expect(downloadLink).toHaveText('download');
    await expect(downloadLink).toHaveAttribute('href', '/api/v1/render/render_final_123/download');
  });
});
