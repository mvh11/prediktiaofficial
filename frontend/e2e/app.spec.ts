import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { expect, test, type Page } from '@playwright/test';

const require = createRequire(import.meta.url);
const axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

const PAGES = [
  { path: '/', heading: /built on evidence/i },
  { path: '/fixtures', heading: 'Fixture browser' },
  { path: '/research', heading: 'Quantitative research approach' },
  { path: '/methodology', heading: 'Methodology and evidence' },
  { path: '/not-a-page', heading: 'Page not found' },
] as const;

const isMobileProject = (name: string) => name === 'mobile';

/** Fails the test on uncaught page errors or console errors other than expected failed API calls. */
function trackConsole(page: Page): string[] {
  const problems: string[] = [];
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    // The browser logs failed /api responses (backend intentionally absent in this suite).
    if (/Failed to load resource/.test(text)) return;
    problems.push(`console: ${text}`);
  });
  return problems;
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'page must not scroll horizontally').toBeLessThanOrEqual(0);
}

async function runAxe(page: Page) {
  await page.addScriptTag({ content: axeSource });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: { id: string; nodes: { target: string[] }[] }[] }> } }).axe;
    const result = await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] },
    });
    return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  });
}

for (const { path, heading } of PAGES) {
  test(`${path} renders, has no console errors and no horizontal overflow`, async ({ page }) => {
    const problems = trackConsole(page);
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    await expect(page.getByRole('contentinfo')).toContainText('Nothing on this site is betting or financial advice');
    await expectNoHorizontalOverflow(page);
    expect(problems).toEqual([]);
  });

  for (const colorScheme of ['light', 'dark'] as const) {
    test(`${path} passes axe (incl. colour contrast) in ${colorScheme} mode`, async ({ page }) => {
      await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
      if (path === '/fixtures') await expect(page.getByRole('alert')).toBeVisible();
      expect(await runAxe(page)).toEqual([]);
    });
  }
}

test('navigation layout adapts to the viewport', async ({ page }, testInfo) => {
  await page.goto('/');
  const primary = page.getByRole('navigation', { name: 'Primary' });
  const toggle = page.getByRole('button', { name: 'Menu' });
  if (isMobileProject(testInfo.project.name)) {
    await expect(primary).toBeHidden();
    await expect(toggle).toBeVisible();
  } else {
    await expect(primary).toBeVisible();
    await expect(toggle).toBeHidden();
  }
});

test('mobile menu is keyboard operable', async ({ page }, testInfo) => {
  test.skip(!isMobileProject(testInfo.project.name), 'mobile-only control');
  await page.goto('/');
  const toggle = page.getByRole('button', { name: 'Menu' });
  await toggle.focus();
  await page.keyboard.press('Enter');
  const mobileNav = page.getByRole('navigation', { name: 'Mobile' });
  await expect(mobileNav).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.keyboard.press('Escape');
  await expect(mobileNav).toBeHidden();
  await expect(toggle).toBeFocused();

  await page.keyboard.press('Enter');
  await mobileNav.getByRole('link', { name: 'Methodology' }).click();
  await expect(page).toHaveURL(/\/methodology$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Methodology and evidence' })).toBeFocused();
  await expect(mobileNav).toBeHidden();
});

test('skip link becomes visible on focus and moves focus to main', async ({ page }) => {
  await page.goto('/research');
  await page.keyboard.press('Tab');
  const skip = page.getByRole('link', { name: 'Skip to main content' });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press('Enter');
  await expect(page.locator('main#main')).toBeFocused();
});

test('without a backend, the fixture browser is honestly unavailable', async ({ page }) => {
  await page.goto('/fixtures');
  await expect(page.getByRole('alert')).toContainText(/Data service unavailable|Request timed out/);
  await expect(page.getByText('Data service:')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Fixtures' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('client-side routing survives a hard reload on a deep link', async ({ page }) => {
  await page.goto('/methodology');
  await page.getByRole('navigation', { name: 'On this page' }).getByRole('link', { name: 'ABSTAIN-first behavior' }).click();
  await expect(page).toHaveURL(/#abstain$/);
  await expect(page.locator('#abstain')).toBeInViewport();
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Methodology and evidence' })).toBeVisible();
});
