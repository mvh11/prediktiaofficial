import { expect, test, type Page } from '@playwright/test';
import { expectNoHorizontalOverflow } from './support/checks.ts';
import { serveDevOnlyApi } from './support/devOnlyApi.ts';

function results(page: Page) {
  return page.getByRole('region', { name: /^Resultados/ });
}

/** On phones the filter form is collapsed behind a disclosure button. */
async function openFilters(page: Page) {
  const toggle = page.getByRole('button', { name: 'Mostrar filtros' });
  if (await toggle.isVisible()) await toggle.click();
}

function rows(page: Page) {
  return results(page).locator('li[data-fixture-id]');
}

test('discovery journey: competition → team → filtered fixtures → detail → back with the same filters', async ({ page }) => {
  await serveDevOnlyApi(page);
  await page.goto('/');
  await page.getByRole('link', { name: 'Explorar competiciones' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Competiciones' })).toBeVisible();

  await page.getByRole('searchbox', { name: 'Buscar competición' }).fill('norte');
  await expect(page.getByText('1 de 3 competiciones')).toBeVisible();
  await page.getByRole('link', { name: 'DEV_ONLY Liga Norte', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'DEV_ONLY Liga Norte' })).toBeVisible();

  await page.getByRole('searchbox', { name: 'Buscar equipo' }).fill('nandu');
  await page.getByRole('link', { name: 'Partidos de DEV_ONLY Deportes Ñandú' }).click();
  await expect(page).toHaveURL(/\/partidos\?competicion=1&temporada=2030&equipo=11$/);
  await expect(rows(page).first()).toBeVisible();
  for (const row of await rows(page).all()) await expect(row).toContainText('DEV_ONLY Deportes Ñandú');

  await openFilters(page);
  await page.getByLabel('Estado', { exact: true }).selectOption({ label: 'Finalizados (tiempo reglamentario, alargue o penales)' });
  await page.getByRole('button', { name: 'Aplicar filtros' }).click();
  await expect(page).toHaveURL(/estado=finalizados/);
  await expect(page.getByRole('button', { name: 'Quitar filtro: Equipo: DEV_ONLY Deportes Ñandú' })).toBeVisible();

  // The URL is the source of truth: a reload restores the same view.
  const filtered = page.url();
  await page.reload();
  await expect(page.getByLabel('Estado', { exact: true })).toHaveValue('finished');
  await expect(page.getByLabel('Equipo', { exact: true })).toHaveValue('11');
  await expect(rows(page).first()).toBeVisible();
  const count = await rows(page).count();
  expect(count).toBeGreaterThan(0);

  await rows(page).first().getByRole('link').click();
  await expect(page.getByRole('heading', { level: 1, name: /vs/ })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Contexto de investigación' })).toBeVisible();
  await page.getByRole('link', { name: /Volver a los partidos/ }).click();
  await expect(page).toHaveURL(filtered);
  await expect(rows(page)).toHaveCount(count);
});

test('pagination uses a lookahead row: 30 fixtures are 25 + 5 and "Next" is then disabled', async ({ page }) => {
  const requests = await serveDevOnlyApi(page);
  await page.goto('/partidos?competicion=1');
  await expect(page.getByText('Partidos 1 a 25, ordenados por fecha y hora de inicio.')).toBeVisible();
  await expect(rows(page)).toHaveCount(25);
  const pagination = page.getByRole('navigation', { name: 'Paginación' });
  await expect(pagination.getByRole('button', { name: 'Página anterior' })).toBeDisabled();

  await pagination.getByRole('button', { name: 'Página siguiente' }).click();
  await expect(page).toHaveURL(/pagina=2$/);
  await expect(page.getByText('Partidos 26 a 30, ordenados por fecha y hora de inicio.')).toBeVisible();
  await expect(rows(page)).toHaveCount(5);
  await expect(pagination.getByRole('button', { name: 'Página siguiente' })).toBeDisabled();
  await expect(page.getByText('Fin de los resultados para estos filtros.')).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: /^Resultados/ })).toBeFocused();

  const limits = requests.filter((r) => r.url.pathname === '/api/fixtures').map((r) => r.url.searchParams.get('limit'));
  expect(new Set(limits)).toEqual(new Set(['26']));

  await page.goBack();
  await expect(page.getByText('Partidos 1 a 25, ordenados por fecha y hora de inicio.')).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test('an exact multiple of 25 ends on a full page without an empty "next" page', async ({ page }) => {
  await serveDevOnlyApi(page);
  await page.goto('/partidos?competicion=2');
  await expect(rows(page)).toHaveCount(25);
  await expect(page.getByText('Fin de los resultados para estos filtros.')).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Paginación' })).toHaveCount(0);
});

test('a deep-linked page beyond the end is explained and offers a way back', async ({ page }) => {
  await serveDevOnlyApi(page);
  await page.goto('/partidos?competicion=2&pagina=3');
  await expect(page.getByRole('heading', { name: 'No hay partidos en esta página' })).toBeVisible();
  await expect(page.getByText(/no informa el total de resultados/)).toBeVisible();
  await page.getByRole('button', { name: 'Ir a la primera página' }).click();
  await expect(page).toHaveURL(/\/partidos\?competicion=2$/);
  await expect(rows(page)).toHaveCount(25);
});

test('empty results and competitions without a current season are explained', async ({ page }) => {
  await serveDevOnlyApi(page);
  await page.goto('/partidos?competicion=3');
  await expect(page.getByText(/no tiene temporada actual registrada; se muestran todas las temporadas/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'No se encontraron partidos' })).toBeVisible();

  await page.goto('/partidos?desde=2031-01-01&hasta=2031-01-31');
  await expect(page.getByRole('heading', { name: 'No se encontraron partidos' })).toBeVisible();
  await page.getByRole('button', { name: /Quitar filtro: Desde/ }).click();
  await expect(page).toHaveURL(/\/partidos\?hasta=2031-01-31$/);
});

test('rows are grouped by Chilean day and show Chile time', async ({ page }) => {
  await serveDevOnlyApi(page);
  await page.goto('/partidos?competicion=1&hasta=2030-03-02');
  // 23:30 UTC on 1 and 2 March is 20:30 in Santiago on the same days.
  await expect(results(page).getByRole('heading', { level: 3 })).toHaveText([/1 de marzo de 2030/, /2 de marzo de 2030/]);
  await expect(rows(page).first()).toContainText('20:30');
  await expect(page.getByText('Horas en hora de Chile continental (America/Santiago).')).toBeVisible();
});

test('on phones the filters collapse behind an accessible disclosure; active filters stay visible', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'disclosure only below 768px');
  await serveDevOnlyApi(page);
  await page.goto('/partidos?competicion=1');
  const toggle = page.getByRole('button', { name: 'Mostrar filtros' });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('form', { name: 'Filtros de partidos' })).toBeHidden();
  await expect(page.getByRole('button', { name: 'Quitar filtro: Competición: DEV_ONLY Liga Norte' })).toBeVisible();
  const panel = page.getByRole('region', { name: 'Filtros' });
  const collapsed = (await panel.boundingBox())?.height ?? 0;

  await toggle.click();
  await expect(page.getByRole('button', { name: 'Ocultar filtros' })).toHaveAttribute('aria-expanded', 'true');
  const expanded = (await panel.boundingBox())?.height ?? 0;
  // Collapsing saves most of the panel's height, so results start much closer to the top.
  expect(collapsed).toBeGreaterThan(0);
  expect(expanded).toBeGreaterThan(collapsed * 3);
  await page.getByLabel('Equipo', { exact: true }).selectOption({ label: 'DEV_ONLY Club Andino' });
  await page.getByRole('button', { name: 'Aplicar filtros' }).click();
  await expect(page).toHaveURL(/equipo=13/);
  await expect(page.getByRole('form', { name: 'Filtros de partidos' })).toBeHidden();
  await expect(page.getByRole('button', { name: 'Quitar filtro: Equipo: DEV_ONLY Club Andino' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
