import { expect, test } from '@playwright/test';
import { runAxe, trackConsole } from './support/checks.ts';
import { DEV_FIXTURES, serveDevOnlyApi } from './support/devOnlyApi.ts';

test('without a backend, every data view is honestly unavailable and offers a retry', async ({ page }) => {
  const problems = trackConsole(page);
  // No fake API: /api is proxied to a port where nothing listens.
  await page.goto('/partidos');
  const alert = page.getByRole('region', { name: /^Resultados/ }).getByRole('alert');
  await expect(alert).toContainText(/Servicio de datos no disponible|La solicitud tardó demasiado/);
  await expect(page.getByText('No disponible', { exact: true })).toBeVisible();
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(0);
  await expect(alert.getByRole('button', { name: 'Reintentar' })).toBeVisible();
  await expect(page.getByLabel('Competición')).toBeDisabled();

  await page.goto('/competiciones');
  await expect(page.getByRole('alert')).toContainText(/Servicio de datos no disponible|La solicitud tardó demasiado/);
  expect(problems).toEqual([]);
});

test('a slow backend shows loading, then a "taking longer" hint, then the data', async ({ page }) => {
  await serveDevOnlyApi(page, { fixturesDelayMs: 6_000 });
  await page.goto('/partidos?competicion=1');
  const status = page.getByRole('status').filter({ hasText: 'Cargando partidos…' });
  await expect(status).toBeVisible();
  await expect(status).toContainText('Está tardando más de lo habitual', { timeout: 8_000 });
  await expect(page.getByText('Partidos 1 a 25, ordenados por fecha y hora de inicio.')).toBeVisible({ timeout: 10_000 });
});

test('a server error is explained and recovers on retry', async ({ page }) => {
  let calls = 0;
  await serveDevOnlyApi(page, {
    override: {
      '/fixtures/101': async (route) => {
        calls += 1;
        if (calls === 1) {
          await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Error interno' }) });
          return;
        }
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(DEV_FIXTURES[0]) });
      },
    },
  });
  await page.goto('/partidos/101');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Error del servicio de datos');
  await expect(alert).toContainText('HTTP 500');
  await expect(alert).not.toContainText('Error interno');
  await alert.getByRole('button', { name: 'Reintentar' }).click();
  await expect(page.getByRole('heading', { level: 1, name: /vs/ })).toBeVisible();
});

test('one malformed record rejects the page with an understandable, recoverable error', async ({ page }) => {
  const broken = [DEV_FIXTURES[0], { ...DEV_FIXTURES[1], kickoff_at: '2030-03-02 23:30' }, DEV_FIXTURES[2]];
  let calls = 0;
  await serveDevOnlyApi(page, {
    override: {
      '/fixtures': async (route) => {
        calls += 1;
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(calls === 1 ? broken : DEV_FIXTURES.slice(0, 3)) });
      },
    },
  });
  await page.goto('/partidos');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Respuesta con formato inesperado');
  await expect(alert).toContainText('registro n.º 2 de la respuesta (identificador 102)');
  await expect(alert).toContainText('«kickoff_at»');
  await expect(alert).toContainText('no elimina ni corrige datos en silencio');
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(0);
  expect(await runAxe(page)).toEqual([]);
  await alert.getByRole('button', { name: 'Reintentar' }).click();
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(3);
});

test('going offline is announced, failed loads say so, and they retry when the connection returns', async ({ page, context }) => {
  const network = { offline: false };
  await serveDevOnlyApi(page, { network });
  await page.goto('/partidos?competicion=1');
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(25);

  network.offline = true;
  await context.setOffline(true);
  await expect(page.getByText('Sin conexión a internet.')).toBeVisible();
  await page.getByRole('button', { name: 'Página siguiente' }).click();
  await expect(page.getByRole('alert')).toContainText('Sin conexión a internet');

  network.offline = false;
  await context.setOffline(false);
  await expect(page.getByText('Sin conexión a internet.')).toBeHidden();
  // No click needed: the failed request is retried automatically.
  await expect(page.getByText('Partidos 26 a 30, ordenados por fecha y hora de inicio.')).toBeVisible();
});
