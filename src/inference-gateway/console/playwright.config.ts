import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'console.spec.ts',
  workers: 2,
  timeout: 30000,
  // Dates render in the browser's locale and time zone; pin both so results match on every machine.
  use: { baseURL: 'http://127.0.0.1:4175', viewport: { width: 1440, height: 1000 }, locale: 'en-US', timezoneId: 'UTC' },
  webServer: {
    command: 'npx vite preview --host 127.0.0.1 --port 4175 --strictPort',
    url: 'http://127.0.0.1:4175/console/',
    reuseExistingServer: false,
  },
});
