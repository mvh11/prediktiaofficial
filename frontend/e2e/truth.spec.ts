import { expect, test } from '@playwright/test';
import { expectNoForbiddenContent } from './support/checks.ts';
import { useDevOnlyApi } from './support/devOnlyApi.ts';

const ALLOWED = [/^\/api\/health$/, /^\/api\/competitions$/, /^\/api\/competitions\/\d+\/teams$/, /^\/api\/fixtures$/, /^\/api\/fixtures\/\d+$/];

test('fixture detail resolves to ABSTAIN with NOT_CERTIFIED and WAIT_REAL_SOURCE explained', async ({ page }) => {
  await useDevOnlyApi(page);
  await page.goto('/partidos/101');
  const output = page.getByRole('region', { name: 'Salida del modelo' });
  await expect(output.getByText('ABSTAIN', { exact: true })).toBeVisible();
  await expect(output.getByText('NOT_CERTIFIED', { exact: true })).toBeVisible();
  await expect(output.getByText('WAIT_REAL_SOURCE', { exact: true })).toBeVisible();
  await expect(output).toContainText('no se muestra ninguna probabilidad, cuota, valor esperado ni recomendación');
  await output.getByRole('link', { name: 'Por qué PREDIKTIA se abstiene' }).click();
  await expect(page).toHaveURL(/\/metodologia#abstencion$/);
  await expect(page.locator('#abstencion')).toBeInViewport();
});

test('every listed fixture carries ABSTAIN, and in-play data is never presented as live or final', async ({ page }) => {
  await useDevOnlyApi(page);
  await page.goto('/partidos?competicion=1');
  const rows = page.locator('li[data-fixture-id]');
  await expect(rows).toHaveCount(25);
  await expect(page.locator('li[data-fixture-id]').getByText('ABSTAIN', { exact: true })).toHaveCount(25);
  await expect(page.locator('li[data-fixture-id="111"]')).toContainText('al sincronizar');

  await page.goto('/partidos/111');
  await expect(page.getByRole('row', { name: /Marcador en la última sincronización/ })).toBeVisible();
  await expect(page.getByText('Resultado final', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/minuto 67 registrado\). No es un marcador en vivo\./)).toBeVisible();
});

test('pages show no probabilities, odds, EV or betting calls to action, and only GET the verified endpoints', async ({ page }) => {
  const requests = await useDevOnlyApi(page);
  for (const path of ['/', '/competiciones', '/competiciones/1', '/partidos?competicion=1', '/partidos/101', '/investigacion', '/metodologia']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await expectNoForbiddenContent(page);
  }
  expect(requests.length).toBeGreaterThan(0);
  for (const request of requests) {
    expect(request.method).toBe('GET');
    expect(ALLOWED.some((pattern) => pattern.test(request.url.pathname)), request.url.pathname).toBe(true);
  }
});
