import { expect, test, type Page } from '@playwright/test';
import { expectNoHorizontalOverflow, runAxe, trackConsole } from './support/checks.ts';
import { useDevOnlyApi } from './support/devOnlyApi.ts';

interface PageCase {
  readonly path: string;
  readonly heading: string | RegExp;
  /** Waits until the data-driven part of the page has rendered. */
  readonly ready?: (page: Page) => Promise<void>;
}

const PAGES: readonly PageCase[] = [
  { path: '/', heading: /basadas en evidencia/i, ready: (page) => expect(page.getByText('Conectado', { exact: true })).toBeVisible() },
  { path: '/competiciones', heading: 'Competiciones', ready: (page) => expect(page.getByRole('link', { name: 'DEV_ONLY Liga Norte', exact: true })).toBeVisible() },
  {
    path: '/competiciones/1',
    heading: 'DEV_ONLY Liga Norte',
    ready: async (page) => {
      await expect(page.getByRole('list', { name: 'Equipos' })).toBeVisible();
      await expect(page.getByText('Cargando próximos partidos…')).toHaveCount(0);
    },
  },
  { path: '/partidos', heading: 'Partidos', ready: (page) => expect(page.getByText(/^Partidos 1 a 25,/)).toBeVisible() },
  {
    path: '/partidos?competicion=1&estado=finalizados&equipo=11',
    heading: 'Partidos',
    ready: (page) => expect(page.getByText(/^Partidos 1 a \d+,/)).toBeVisible(),
  },
  { path: '/partidos/101', heading: /^DEV_ONLY Deportes Ñandú vs/, ready: (page) => expect(page.getByRole('link', { name: 'DEV_ONLY Liga Norte', exact: true })).toBeVisible() },
  { path: '/partidos/111', heading: /vs/, ready: (page) => expect(page.getByText(/No es un marcador en vivo/)).toBeVisible() },
  { path: '/investigacion', heading: 'Enfoque de investigación cuantitativa' },
  { path: '/metodologia', heading: 'Metodología y evidencia' },
  { path: '/no-existe', heading: 'Página no encontrada' },
  { path: '/partidos/99999999999999999999', heading: 'Identificador fuera del rango admitido' },
];

async function open(page: Page, item: PageCase) {
  await useDevOnlyApi(page);
  await page.goto(item.path);
  await expect(page.getByRole('heading', { level: 1, name: item.heading })).toBeVisible();
  if (item.ready) await item.ready(page);
}

for (const item of PAGES) {
  test(`${item.path} renders in Spanish without console errors or horizontal overflow`, async ({ page }) => {
    const problems = trackConsole(page);
    await open(page, item);
    await expect(page.locator('html')).toHaveAttribute('lang', 'es-CL');
    await expect(page.getByRole('contentinfo')).toContainText('Nada en este sitio constituye asesoría de apuestas ni financiera');
    await expectNoHorizontalOverflow(page);
    expect(problems).toEqual([]);
  });

  for (const colorScheme of ['light', 'dark'] as const) {
    test(`${item.path} passes axe (incl. colour contrast) with the system in ${colorScheme} mode`, async ({ page }) => {
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await open(page, item);
      expect(await runAxe(page)).toEqual([]);
    });
  }
}

// The viewer's explicit choice overrides the system preference, in both directions.
for (const [system, chosen] of [
  ['light', 'dark'],
  ['dark', 'light'],
] as const) {
  for (const item of [PAGES[0], PAGES[3], PAGES[5]] as const) {
    test(`${item.path} passes axe with the ${chosen} theme chosen while the system is ${system}`, async ({ page }) => {
      await page.addInitScript((theme) => {
        window.localStorage.setItem('prediktia.theme', theme);
      }, chosen);
      await page.emulateMedia({ colorScheme: system, reducedMotion: 'reduce' });
      await open(page, item);
      await expect(page.locator('html')).toHaveAttribute('data-theme', chosen);
      const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      expect(background).toBe(chosen === 'dark' ? 'rgb(10, 16, 32)' : 'rgb(245, 247, 250)');
      expect(await runAxe(page)).toEqual([]);
    });
  }
}
