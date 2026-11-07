import { expect, test } from '@playwright/test';
import { DEV_FIXTURES, useDevOnlyApi } from './support/devOnlyApi.ts';

for (const path of ['/partidos/abc', '/partidos/007', '/partidos/-5', '/competiciones/xyz', '/competiciones/0']) {
  test(`${path} is reported as an invalid id and requests nothing`, async ({ page }) => {
    const requests = await useDevOnlyApi(page);
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1, name: 'Identificador no válido' })).toBeVisible();
    expect(requests.filter((r) => /^\/api\/(fixtures|competitions)/.test(r.url.pathname))).toEqual([]);
  });
}

for (const path of ['/partidos/99999999999999999999', '/partidos/9007199254740993', '/competiciones/2147483648']) {
  test(`${path} is reported as out of range, never rounded or requested`, async ({ page }) => {
    const requests = await useDevOnlyApi(page);
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1, name: 'Identificador fuera del rango admitido' })).toBeVisible();
    const raw = path.split('/').pop() ?? '';
    await expect(page.getByText(`El identificador ${raw} supera el máximo admitido por el servicio de datos (2.147.483.647)`, { exact: false })).toBeVisible();
    expect(requests.filter((r) => /^\/api\/(fixtures|competitions)/.test(r.url.pathname))).toEqual([]);
  });
}

test('an out-of-range id in the filters is reported and not sent', async ({ page }) => {
  const requests = await useDevOnlyApi(page);
  await page.goto('/partidos?competicion=99999999999999999999');
  await expect(page.getByText('competicion = «99999999999999999999» (fuera del rango admitido)')).toBeVisible();
  await expect(page.locator('li[data-fixture-id]').first()).toBeVisible();
  const sent = requests.filter((r) => r.url.pathname === '/api/fixtures');
  expect(sent.length).toBeGreaterThan(0);
  for (const request of sent) expect(request.url.searchParams.has('competition_id')).toBe(false);
});

test('a response with an integer JSON cannot represent exactly is refused, not rounded', async ({ page }) => {
  const body = JSON.stringify(DEV_FIXTURES[0]).replace('"id":101,', '"id":9007199254740993,');
  await useDevOnlyApi(page, {
    override: {
      '/fixtures/101': (route) => route.fulfill({ status: 200, contentType: 'application/json', body }),
    },
  });
  await page.goto('/partidos/101');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Identificador numérico no representable');
  await expect(alert).toContainText('9007199254740993');
  await expect(page.getByText('9007199254740992')).toHaveCount(0);
});

test('unknown but valid ids are "not found", not errors', async ({ page }) => {
  await useDevOnlyApi(page);
  await page.goto('/partidos/2147483647');
  await expect(page.getByRole('heading', { level: 1, name: 'Partido no encontrado' })).toBeVisible();
  await page.goto('/competiciones/2147483647');
  await expect(page.getByRole('heading', { level: 1, name: 'Competición no encontrada' })).toBeVisible();
});
