import { expect, test, type Page, type Route } from '@playwright/test';
import { expectNoHorizontalOverflow } from './support/checks.ts';
import { DEV_FIXTURES, serveDevOnlyApi } from './support/devOnlyApi.ts';

/**
 * D1 (independent QA): JSON.parse turns 12.9999999999999999 into 13, so a response could make the UI show — or link
 * to — a different fixture than the one requested. Identifier tokens are now checked on the raw response text and
 * the detail id must equal the requested id. Bodies below are raw JSON text so the exact token reaches the browser.
 */

const base = DEV_FIXTURES[0];

function rawJson(route: Route, text: string, status = 200, contentType = 'application/json') {
  return route.fulfill({ status, contentType, body: text });
}

/** DEV_ONLY fixture 101 as JSON text, with its `"id":101` token replaced by `token`. */
function fixtureWithIdToken(token: string): string {
  const text = JSON.stringify(base).replace('"id":101,', `"id":${token},`);
  if (!text.includes(`"id":${token},`)) throw new Error('token not placed');
  return text;
}

async function expectNothingShownOrLinked(page: Page) {
  await expect(page.getByRole('heading', { level: 1, name: /vs/ })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Salida del modelo' })).toHaveCount(0);
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(0);
}

for (const [requested, token] of [
  ['13', '12.9999999999999999'],
  ['42', '41.99999999999999999'],
  ['1', '1.0000000000000001'],
  ['13', '1.3e1'],
] as const) {
  test(`detail /partidos/${requested}: id token ${token} is refused, never read as ${requested}`, async ({ page }) => {
    await serveDevOnlyApi(page, { override: { [`/fixtures/${requested}`]: (route) => rawJson(route, fixtureWithIdToken(token)) } });
    await page.goto(`/partidos/${requested}`);
    const alert = page.getByRole('alert');
    await expect(alert).toContainText('Identificador no válido en la respuesta');
    await expect(alert).toContainText(`«${token}»`);
    await expectNothingShownOrLinked(page);
    await expect(page).toHaveURL(new RegExp(`/partidos/${requested}$`));
    await expectNoHorizontalOverflow(page);
  });
}

test('list: a near-integer row id is refused, no row links to the rounded id, and a retry recovers', async ({ page }) => {
  let calls = 0;
  const list = DEV_FIXTURES.slice(0, 3);
  const broken = JSON.stringify(list).replace('"id":102,', '"id":41.99999999999999999,');
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
  await expect(alert).toContainText('Identificador no válido en la respuesta');
  await expect(alert).toContainText('registro n.º 2');
  await expectNothingShownOrLinked(page);
  await expect(page.locator('a[href="/partidos/42"]')).toHaveCount(0);

  await alert.getByRole('button', { name: 'Reintentar' }).click();
  await expect(page.locator('li[data-fixture-id]')).toHaveCount(3);
  await expect(page.locator('a[href="/partidos/42"]')).toHaveCount(0);
  await page.locator('li[data-fixture-id="102"]').getByRole('link').click();
  await expect(page).toHaveURL(/\/partidos\/102$/);
});

test('list: an exponent id token is refused', async ({ page }) => {
  const text = JSON.stringify(DEV_FIXTURES.slice(0, 2)).replace('"id":102,', '"id":1.02e2,');
  await serveDevOnlyApi(page, { override: { '/fixtures': (route) => rawJson(route, text) } });
  await page.goto('/partidos');
  await expect(page.getByRole('alert')).toContainText('«1.02e2»');
  await expectNothingShownOrLinked(page);
});

test('detail: a well-formed response about another fixture is refused and the URL stays', async ({ page }) => {
  const other = DEV_FIXTURES[1];
  await serveDevOnlyApi(page, { override: { '/fixtures/101': (route) => rawJson(route, JSON.stringify(other)) } });
  await page.goto('/partidos/101');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('La respuesta no corresponde al partido solicitado');
  await expect(alert).toContainText('Se pidió el partido 101, pero el servicio de datos respondió con el partido 102.');
  await expectNothingShownOrLinked(page);
  await expect(page.locator('a[href="/partidos/102"]')).toHaveCount(0);
  await expect(page).toHaveURL(/\/partidos\/101$/);
});

test('strings that contain JSON-looking identifiers are data, not identifiers', async ({ page }) => {
  const text = JSON.stringify({ ...base, venue_name: '{"id":12.9999999999999999}', referee: '"id": 1e3' });
  await serveDevOnlyApi(page, { override: { '/fixtures/101': (route) => rawJson(route, text) } });
  await page.goto('/partidos/101');
  await expect(page.getByRole('heading', { level: 1, name: /vs/ })).toBeVisible();
  await expect(page.getByText('{"id":12.9999999999999999}')).toBeVisible();
  await expect(page.getByText('"id": 1e3')).toBeVisible();
});

test('the largest backend id is accepted unchanged', async ({ page }) => {
  const text = JSON.stringify({ ...base, id: 2147483647 });
  await serveDevOnlyApi(page, { override: { '/fixtures/2147483647': (route) => rawJson(route, text) } });
  await page.goto('/partidos/2147483647');
  await expect(page.getByRole('heading', { level: 1, name: /vs/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Salida del modelo' })).toBeVisible();
});

test('a non-JSON 404 from a proxy is "service unavailable", not "fixture not found" (L1)', async ({ page }) => {
  await serveDevOnlyApi(page, {
    override: { '/fixtures/101': (route) => rawJson(route, '<!doctype html><title>Not Found</title>', 404, 'text/html') },
  });
  await page.goto('/partidos/101');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Servicio de datos no disponible');
  await expect(alert).toContainText('HTTP 404');
  await expect(page.getByRole('heading', { level: 1, name: 'Partido no encontrado' })).toHaveCount(0);
});

test('out-of-range UTC days in the URL are reported and never sent (L2)', async ({ page }) => {
  const requests = await serveDevOnlyApi(page);
  await page.goto('/partidos?hasta=9999-12-31');
  await expect(page.getByText('hasta = «9999-12-31» (fuera del rango admitido)')).toBeVisible();
  await expect(page.locator('li[data-fixture-id]').first()).toBeVisible();
  const sent = requests.filter((r) => r.url.pathname === '/api/fixtures');
  expect(sent.length).toBeGreaterThan(0);
  for (const request of sent) expect(request.url.searchParams.has('date_to')).toBe(false);
});
