import { expect, test, type Page, type Route } from '@playwright/test';
import { expectNoHorizontalOverflow } from './support/checks.ts';
import { DEV_COMPETITIONS, DEV_FIXTURES, serveDevOnlyApi } from './support/devOnlyApi.ts';

/**
 * FE2-R1: JSON.parse reads 2029.9999999999999999 as 2030, 0.99999999999999999 as 1 and 6.7e1 as 67, so a season, a
 * score or a minute could be shown rounded. Every integer of the contract is now checked on the raw response text.
 * Bodies are raw JSON text so the exact token reaches the browser.
 */

const RAW = 987_654_321;

function rawJson(route: Route, text: string) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: text });
}

/** JSON text of `value` with its single RAW number replaced by `token`, verbatim. */
function withRawToken(value: unknown, token: string): string {
  const parts = JSON.stringify(value).split(String(RAW));
  if (parts.length !== 2) throw new Error('RAW must occur exactly once');
  return parts.join(token);
}

function devFixture(id: number) {
  const found = DEV_FIXTURES.find((fixture) => fixture.id === id);
  if (!found) throw new Error(`no DEV_ONLY fixture ${String(id)}`);
  return found;
}

async function expectRefused(page: Page, field: string, token: string) {
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Número entero no válido en la respuesta');
  await expect(alert).toContainText(`«${field}»`);
  await expect(alert).toContainText(`«${token}»`);
  await expect(alert).toContainText('se descartó la respuesta completa');
}

for (const [id, field, token] of [
  [101, 'home_goals', '0.99999999999999999'],
  [101, 'season', '2029.9999999999999999'],
  [111, 'elapsed', '6.7e1'],
] as const) {
  test(`detail /partidos/${String(id)}: ${field} token ${token} is refused and nothing of the fixture is shown`, async ({ page }) => {
    const text = withRawToken({ ...devFixture(id), [field]: RAW }, token);
    await serveDevOnlyApi(page, { override: { [`/fixtures/${String(id)}`]: (route) => rawJson(route, text) } });
    await page.goto(`/partidos/${String(id)}`);
    await expectRefused(page, field, token);
    await expect(page.getByRole('heading', { level: 1, name: /vs/ })).toHaveCount(0);
    await expect(page.getByRole('table', { name: /Marcadores registrados/ })).toHaveCount(0);
    await expect(page.getByRole('region', { name: 'Salida del modelo' })).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(`/partidos/${String(id)}$`));
    await expectNoHorizontalOverflow(page);
  });
}

test('list: a near-integer season in one record refuses the page with its record number, and a retry recovers', async ({ page }) => {
  let calls = 0;
  const list = DEV_FIXTURES.slice(0, 3);
  const broken = withRawToken([list[0], { ...list[1], season: RAW }, list[2]], '2029.9999999999999999');
  await serveDevOnlyApi(page, {
    override: {
      '/fixtures': (route) => {
        calls += 1;
        return rawJson(route, calls === 1 ? broken : JSON.stringify(list));
      },
    },
  });
  await page.goto('/partidos?competicion=1');
  const alert = page.getByRole('region', { name: /^Resultados/ }).getByRole('alert');
  await expectRefused(page, 'season', '2029.9999999999999999');
  await expect(alert).toContainText('registro n.º 2');
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(0);

  await alert.getByRole('button', { name: 'Reintentar' }).click();
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(3);
});

test('catalog: an exponent current season refuses the competition list', async ({ page }) => {
  const text = withRawToken([DEV_COMPETITIONS[0], { ...DEV_COMPETITIONS[1], current_season: RAW }], '2.03e3');
  await serveDevOnlyApi(page, { override: { '/competitions': (route) => rawJson(route, text) } });
  await page.goto('/competiciones');
  await expectRefused(page, 'current_season', '2.03e3');
  await expect(page.getByRole('link', { name: 'DEV_ONLY Liga Norte' })).toHaveCount(0);
});

test('exact integers are shown unchanged: score 0-1, and a live minute of 67', async ({ page }) => {
  await serveDevOnlyApi(page);
  await page.goto('/partidos/101');
  const table = page.getByRole('table', { name: /Marcadores registrados/ });
  await expect(table.getByRole('row').nth(1)).toHaveText(/Resultado final\s*0\s*1/);
  await page.goto('/partidos/111');
  await expect(page.getByText(/minuto 67 registrado/)).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});
