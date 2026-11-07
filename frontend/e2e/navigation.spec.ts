import { expect, test, type Page } from '@playwright/test';
import { expectNoHorizontalOverflow, usesMenu } from './support/checks.ts';
import { useDevOnlyApi } from './support/devOnlyApi.ts';

const DESTINATIONS = [
  { label: 'Competiciones', path: '/competiciones', heading: 'Competiciones', title: 'Competiciones · PREDIKTIA' },
  { label: 'Partidos', path: '/partidos', heading: 'Partidos', title: 'Partidos · PREDIKTIA' },
  { label: 'Investigación', path: '/investigacion', heading: 'Enfoque de investigación cuantitativa', title: 'Investigación · PREDIKTIA' },
  { label: 'Metodología', path: '/metodologia', heading: 'Metodología y evidencia', title: 'Metodología · PREDIKTIA' },
  { label: 'Inicio', path: '/', heading: /basadas en evidencia/i, title: 'PREDIKTIA' },
] as const;

async function navigation(page: Page, project: string) {
  if (!usesMenu(project)) return page.getByRole('navigation', { name: 'Principal' });
  await page.getByRole('button', { name: 'Menú' }).click();
  const menu = page.getByRole('navigation', { name: 'Menú móvil' });
  await expect(menu).toBeVisible();
  return menu;
}

test('the header adapts to the viewport', async ({ page }, testInfo) => {
  await page.goto('/');
  const primary = page.getByRole('navigation', { name: 'Principal' });
  const toggle = page.getByRole('button', { name: 'Menú' });
  if (usesMenu(testInfo.project.name)) {
    await expect(primary).toBeHidden();
    await expect(toggle).toBeVisible();
  } else {
    await expect(primary).toBeVisible();
    await expect(toggle).toBeHidden();
  }
});

test('Spanish navigation: every destination updates URL, title, current link and focus', async ({ page }, testInfo) => {
  await useDevOnlyApi(page);
  await page.goto('/');
  for (const destination of DESTINATIONS) {
    const nav = await navigation(page, testInfo.project.name);
    await nav.getByRole('link', { name: destination.label }).click();
    await expect(page).toHaveURL(new RegExp(`${destination.path === '/' ? '/' : destination.path}$`));
    const heading = page.getByRole('heading', { level: 1, name: destination.heading });
    await expect(heading).toBeVisible();
    await expect(page).toHaveTitle(destination.title);
    if (destination.path !== '/') await expect(heading).toBeFocused();
    if (!usesMenu(testInfo.project.name)) {
      await expect(page.getByRole('navigation', { name: 'Principal' }).getByRole('link', { name: destination.label })).toHaveAttribute('aria-current', 'page');
    }
    await expect(page.getByRole('navigation', { name: 'Menú móvil' })).toBeHidden();
  }
});

test('the menu is keyboard operable and returns focus', async ({ page }, testInfo) => {
  test.skip(!usesMenu(testInfo.project.name), 'menu only below 960px');
  await useDevOnlyApi(page);
  await page.goto('/');
  const toggle = page.getByRole('button', { name: 'Menú' });
  await toggle.focus();
  await page.keyboard.press('Enter');
  const menu = page.getByRole('navigation', { name: 'Menú móvil' });
  await expect(menu).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cerrar menú' })).toHaveAttribute('aria-expanded', 'true');
  await expectNoHorizontalOverflow(page);

  await page.keyboard.press('Tab');
  await expect(menu.getByRole('link', { name: 'Inicio' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(toggle).toBeFocused();

  await page.keyboard.press('Enter');
  await menu.getByRole('link', { name: 'Metodología' }).press('Enter');
  await expect(page).toHaveURL(/\/metodologia$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Metodología y evidencia' })).toBeFocused();
  await expect(menu).toBeHidden();
});

test('skip link becomes visible on focus and moves focus to main', async ({ page }) => {
  await page.goto('/investigacion');
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Saltar al contenido principal' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press('Enter');
  await expect(page.locator('main#main')).toBeFocused();
});

test('breadcrumbs lead back up the journey', async ({ page }) => {
  await useDevOnlyApi(page);
  await page.goto('/competiciones/1');
  const crumbs = page.getByRole('navigation', { name: 'Ruta de navegación' });
  await expect(crumbs).toContainText('Inicio');
  await expect(crumbs.getByText('DEV_ONLY Liga Norte')).toHaveAttribute('aria-current', 'page');
  await crumbs.getByRole('link', { name: 'Competiciones' }).click();
  await expect(page).toHaveURL(/\/competiciones$/);
});

test('Phase 1 English links redirect to the Spanish URLs', async ({ page }) => {
  await useDevOnlyApi(page);
  await page.goto('/fixtures?competition=1&status=finished');
  await expect(page).toHaveURL(/\/partidos\?competicion=1&estado=finalizados$/);
  await page.goto('/methodology#abstain');
  await expect(page).toHaveURL(/\/metodologia#abstencion$/);
  await expect(page.locator('#abstencion')).toBeInViewport();
});

test('a deep link with an anchor survives a hard reload', async ({ page }) => {
  await page.goto('/metodologia');
  await page.getByRole('navigation', { name: 'En esta página' }).getByRole('link', { name: 'Comportamiento ABSTAIN primero' }).click();
  await expect(page).toHaveURL(/#abstencion$/);
  await expect(page.locator('#abstencion')).toBeInViewport();
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Metodología y evidencia' })).toBeVisible();
});

test('the theme choice persists across reloads', async ({ page }) => {
  await page.goto('/');
  const group = page.getByRole('group', { name: 'Apariencia' });
  // The transparent radio covers its label (a click anywhere on the option selects it).
  await group.getByRole('radio', { name: 'Oscura' }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(group.getByRole('radio', { name: 'Oscura' })).toBeChecked();
});
