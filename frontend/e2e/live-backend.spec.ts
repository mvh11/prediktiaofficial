import { expect, test } from '@playwright/test';

/**
 * Opt-in smoke test against a REAL running PREDIKTIA backend whose database is
 * deliberately unreachable (see README, "Live backend smoke test"). Run with:
 *
 *   E2E_LIVE_BACKEND=1 PREDIKTIA_API_PROXY_TARGET=http://127.0.0.1:8765 npm run test:e2e -- live-backend
 *
 * Verifies the real /health contract through the proxy and that a 500 from a
 * DB-backed endpoint is shown as an honest unavailable state.
 */
test.skip(process.env.E2E_LIVE_BACKEND !== '1', 'requires E2E_LIVE_BACKEND=1 and a running backend');

test('real backend: health is connected, DB-backed data is honestly unavailable', async ({ page }) => {
  await page.goto('/fixtures');
  await expect(page.getByText('Connected')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Data service unavailable', { timeout: 20_000 });
  await expect(page.getByRole('list', { name: 'Fixtures' })).toHaveCount(0);
});
