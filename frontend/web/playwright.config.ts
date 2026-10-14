import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3100';
const targetPort = Number(new URL(baseURL).port || '3000');
const webServerCommand = targetPort === 3100
  ? 'npm run start -- -p 3100'
  : `npm run dev -- --hostname 127.0.0.1 --port ${targetPort}`;

export default defineConfig({
  testDir: './e2e',
  timeout: 45000,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL,
    trace: 'off',
    video: 'off',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: webServerCommand,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120000,
  },
});
