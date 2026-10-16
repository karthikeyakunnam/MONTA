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
    const platformSelect = page.getByLabel(/Target platform/);
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

  test('4. Renders a stubbed ready state with a video preview and download link', async ({ page }) => {
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
    await expect(page.getByRole('table').getByText('beach_surf.mp4', { exact: true })).toBeVisible();

    // Start processing
    const runBtn = page.locator('button:has-text("Start processing")');
    await expect(runBtn).toBeVisible();
    await runBtn.click();

    // This test stubs the API to exercise the ready-state UI; the real pipeline is covered by backend integration tests.
    const preview = page.locator('video[aria-label*="Cinematic Reel"]');
    await expect(preview).toBeVisible();
    await expect(preview).toHaveAttribute('src', /\/api\/v1\/render\/render_final_123\/download$/);
    const downloadLink = page.getByRole('link', { name: 'Download MP4' });
    await expect(downloadLink).toBeVisible();
    await expect(downloadLink).toHaveAttribute('href', /\/api\/v1\/render\/render_final_123\/download\?download=true$/);
    await expect(downloadLink).toHaveAttribute('download', '');
  });

  test('5. Handles model unavailable or pipeline startup failure with retry', async ({ page }) => {
    const projectId = 'proj_model_unavail_01';
    let startAttempts = 0;

    await page.route(`**/api/v1/projects/${projectId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: projectId,
          title: 'Ollama Unreachable Project',
          description: null,
          status: 'draft',
          status_detail: 'upload footage to begin',
          raw_prompt: 'Cinematic video',
          target_platform: 'youtube',
          clip_count: 1,
          total_duration_s: 5.0,
          clips: [
            {
              id: 'c1',
              status: 'analyzed',
              filename: 'clip1.mp4',
              original_filename: 'clip1.mp4',
              duration: 5.0,
              fps: 30.0,
              resolution: '1920x1080',
              resolution_class: '1080p',
              video_codec: 'h264',
              bitrate: 5000000,
              file_size_bytes: 3000000,
              has_audio: true,
              created_at: new Date().toISOString(),
            },
          ],
          jobs: [],
          quota: [],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
    });

    await page.route(`**/api/v1/projects/${projectId}/events`, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
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

    await page.route(`**/api/v1/projects/${projectId}/pipeline`, async (route) => {
      startAttempts++;
      if (startAttempts === 1) {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Ollama model qwen2.5-coder:1.5b unavailable or connection refused' }),
        });
      } else {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            job_id: 'job_recovered',
            tracking_id: 'job_recovered',
            task_id: 'task_rec',
            state: 'queued',
            project_status: 'queued',
          }),
        });
      }
    });

    await page.goto(`/projects/${projectId}`);

    const runBtn = page.locator('button:has-text("Start processing")');
    await expect(runBtn).toBeVisible();
    await runBtn.click();

    // Verify banner is shown with failure description
    const errorBanner = page.locator('.banner[role="alert"]');
    await expect(errorBanner).toBeVisible();
    await expect(errorBanner).toContainText('The processing queue is unavailable');

    // Click retry
    const retryBtn = errorBanner.locator('button:has-text("Try again")');
    await expect(retryBtn).toBeVisible();
    await retryBtn.click();
    expect(startAttempts).toBe(2);
  });

  test('6. Displays render/pipeline failure details and offers retry', async ({ page }) => {
    const projectId = 'proj_failed_render_01';

    await page.route(`**/api/v1/projects/${projectId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: projectId,
          title: 'Failing Render Project',
          description: null,
          status: 'failed',
          status_detail: 'FFmpeg encoding failed: invalid aspect ratio filter dimensions',
          raw_prompt: 'High octane car reel',
          target_platform: 'reels',
          clip_count: 1,
          total_duration_s: 8.0,
          clips: [
            {
              id: 'c2',
              status: 'analyzed',
              filename: 'race.mp4',
              original_filename: 'race.mp4',
              duration: 8.0,
              fps: 60.0,
              resolution: '1080x1920',
              resolution_class: '1080p',
              video_codec: 'h264',
              bitrate: 10000000,
              file_size_bytes: 5000000,
              has_audio: false,
              created_at: new Date().toISOString(),
            },
          ],
          jobs: [
            {
              id: 'job_failed_01',
              kind: 'pipeline',
              state: 'failed',
              stage: 'rendering',
              task_id: 'task_f01',
              attempts: 1,
              error: 'FFmpeg encoding failed: invalid aspect ratio filter dimensions',
              created_at: new Date().toISOString(),
              started_at: new Date().toISOString(),
              finished_at: new Date().toISOString(),
            },
          ],
          quota: [],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
    });

    await page.route(`**/api/v1/projects/${projectId}/events`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { project_id: projectId, stage: 'rendering', message: 'FFmpeg encoding failed', at: '2026-10-14T08:00:00Z' },
        ]),
      });
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

    // Verify failure banner is shown
    const failureBanner = page.locator('.banner.error');
    await expect(failureBanner).toBeVisible();
    await expect(failureBanner).toContainText('Processing failed');
    await expect(failureBanner).toContainText('FFmpeg encoding failed');

    // Retry uses this failed job's saved inputs and the existing uploaded clips.
    const retryBtn = failureBanner.locator('button:has-text("Retry this revision")');
    await expect(retryBtn).toBeVisible();
  });

  test('7. Persists advanced creative controls in a same-project revision form', async ({ page }) => {
    const projectId = 'proj_studio_controls_01';
    page.on('requestfailed', (request) => console.log('request failed:', request.url(), request.failure()?.errorText));
    const baseOptions = {
      target_duration_s: 12, color_grade: 'dark', caption_style: 'None', music_style: null,
      add_music: false, aspect_ratio: '9:16', style_preset: null, pace: null, transition: null,
      transition_duration_ms: null, scale_mode: null, brightness: null, contrast: null, saturation: null,
      gamma: null, shadow_coolness: null, highlight_warmth: null, vignette: null,
    };
    let savedOptions = baseOptions;
    const submitted: { value: { prompt?: string; edit_options?: typeof baseOptions } | null } = { value: null };
    let prompt = 'A shadowy motivational reel';
    let promptVersion = 1;
    const projectDetail = () => ({
      id: projectId, title: 'Studio controls', description: null, status: 'complete', status_detail: null,
      raw_prompt: prompt, target_platform: 'instagram', edit_options: savedOptions, prompt_version: promptVersion,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(), clip_count: 1,
      clips: [{
        id: 'clip_studio_1', status: 'analyzed', filename: 'clip.mp4', original_filename: 'clip.mp4',
        format: 'mp4', duration: 8, fps: 30, resolution: '1080x1920', resolution_class: '1080p',
        video_codec: 'h264', bitrate: 1000000, file_size_bytes: 1000000, has_audio: true,
        audio_streams: [], sha256: 'fixture', duplicate_of: null, rejection_issues: null, created_at: null,
        preview_url: null,
      }],
      jobs: [], quota: [], total_duration_s: 8, story_pattern: null, story_duration_s: null,
      story_cuts: null, story_score: null, render_status: null, render_url: null,
      render_resolution: null, render_duration_s: null, render_size_bytes: null, renders: [],
    });
    await page.route(`**/api/v1/projects/${projectId}`, async (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(projectDetail()) }));
    await page.route(`**/api/v1/projects/${projectId}/events`, async (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
    await page.route(`**/api/v1/timeline/${projectId}`, async (route) =>
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'No generated timeline' }) }));
    await page.route('**/api/v1/upload/limits', async (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        allowed_extensions: ['.mp4'], max_file_bytes: 500000000, max_request_bytes: 1000000000,
        max_files_per_request: 20, max_clip_seconds: 600, min_clip_seconds: 1, resolution_classes: [],
      }) }));
    await page.route(`**/api/v1/projects/${projectId}/revisions`, async (route) => {
      const revision = JSON.parse(route.request().postData() || '{}') as typeof submitted.value;
      submitted.value = revision;
      prompt = revision?.prompt ?? prompt;
      savedOptions = revision?.edit_options ?? savedOptions;
      promptVersion += 1;
      await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({
        tracking_id: 'job_studio_revision', job_id: 'job_studio_revision', task_id: 'task_studio_revision',
        state: 'queued', project_status: 'queued',
      }) });
    });

    await page.goto(`/projects/${projectId}`);
    await page.getByRole('button', { name: 'Revise edit' }).click();
    const form = page.locator('form.edit-options');
    await form.locator('details.advanced-controls > summary').click();
    await form.getByLabel('Editing prompt').fill('A shadowy motivational reel with a hopeful ending');
    await form.getByLabel('Style preset').selectOption('trailer');
    await form.getByLabel('Cut pace').selectOption('fast');
    await form.getByRole('combobox', { name: 'Transition', exact: true }).selectOption('crossfade');
    await form.getByLabel('Transition duration · milliseconds').fill('325');
    await form.getByLabel('Composition').selectOption('fill');
    const coolShadows = form.getByRole('slider', { name: 'Cool shadows' });
    for (let step = 0; step < 30; step += 1) await coolShadows.press('ArrowRight');
    const warmHighlights = form.getByRole('slider', { name: 'Warm highlights' });
    for (let step = 0; step < 40; step += 1) await warmHighlights.press('ArrowRight');
    await form.getByRole('button', { name: 'Create new version' }).click();
    await expect.poll(() => submitted.value).not.toBeNull();
    expect(submitted.value?.edit_options).toMatchObject({
      style_preset: 'trailer', pace: 'fast', transition: 'crossfade', transition_duration_ms: 325,
      scale_mode: 'fill', shadow_coolness: 0.3, highlight_warmth: 0.4,
    });
    expect(await page.getByRole('heading', { name: 'Studio controls' }).isVisible()).toBe(true);

    await page.getByRole('button', { name: 'Revise edit' }).click();
    const reopened = page.locator('form.edit-options');
    await reopened.locator('details.advanced-controls > summary').click();
    await expect(reopened.getByLabel('Style preset')).toHaveValue('trailer');
    await expect(reopened.getByLabel('Cut pace')).toHaveValue('fast');
    await expect(reopened.getByRole('combobox', { name: 'Transition', exact: true })).toHaveValue('crossfade');
    await expect(reopened.getByLabel('Composition')).toHaveValue('fill');
    await expect(reopened.getByRole('slider', { name: 'Cool shadows' })).toHaveValue('0.3');
    await expect(reopened.getByRole('slider', { name: 'Warm highlights' })).toHaveValue('0.4');
  });
});
