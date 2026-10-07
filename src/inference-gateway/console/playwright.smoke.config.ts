import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'smoke.spec.ts',
  workers: 1,
  timeout: 180000,
  use: {
    baseURL: process.env.AGENTWORKFLOWS_GATEWAY_URL || `http://127.0.0.1:${process.env.AGENTWORKFLOWS_GATEWAY_PORT || 8080}`,
    viewport: { width: 1440, height: 1000 },
  },
});
