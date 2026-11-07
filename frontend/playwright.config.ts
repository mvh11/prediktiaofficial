import { defineConfig } from '@playwright/test';

/**
 * Real-browser checks against the PRODUCTION build served by `vite preview`.
 *
 * By default the /api proxy points at a port where nothing listens, so the
 * suite verifies the honest "backend unavailable" behaviour. Point
 * PREDIKTIA_API_PROXY_TARGET at a running backend to smoke-test integration.
 *
 * Uses the locally installed Microsoft Edge (`channel: 'msedge'`) so no
 * browser download is needed; set PW_CHANNEL to override (e.g. "chrome").
 */
const PORT = 4173;
const channel = process.env.PW_CHANNEL ?? 'msedge';
const proxyTarget = process.env.PREDIKTIA_API_PROXY_TARGET ?? 'http://127.0.0.1:9';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1280, height: 800 } } },
    { name: 'tablet', use: { viewport: { width: 820, height: 1180 }, hasTouch: true } },
    { name: 'mobile', use: { viewport: { width: 360, height: 740 }, hasTouch: true, isMobile: true } },
  ],
  webServer: {
    command: `npx vite build && npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { PREDIKTIA_API_PROXY_TARGET: proxyTarget },
  },
});
