import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { expect, type Page } from '@playwright/test';

const require = createRequire(import.meta.url);
const axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

export const isMobileProject = (name: string) => name === 'mobile';
/** Five destinations fit in the header from 960px; narrower viewports use the menu. */
export const usesMenu = (name: string) => name === 'mobile' || name === 'tablet';

/** Collects uncaught page errors and console errors, except browser logs for intentionally failed /api responses. */
export function trackConsole(page: Page): string[] {
  const problems: string[] = [];
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    if (/Failed to load resource|ERR_INTERNET_DISCONNECTED|ERR_CONNECTION_REFUSED/.test(text)) return;
    problems.push(`console: ${text}`);
  });
  return problems;
}

export async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'page must not scroll horizontally').toBeLessThanOrEqual(0);
}

export async function runAxe(page: Page): Promise<string[]> {
  await page.addScriptTag({ content: axeSource });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx: Document, opts: object) => Promise<{ violations: { id: string; nodes: { target: string[] }[] }[] }> } }).axe;
    const result = await axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] },
    });
    return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
  });
}

/** Text that must never appear: probabilities, prices, EV, stakes, betting calls to action. */
export const FORBIDDEN_PATTERNS: readonly [string, RegExp][] = [
  ['percentage', /\d\s*%/],
  ['decimal odds or probability', /\b\d+[.,]\d+\b/],
  ['fractional odds', /\b\d+\/\d+\b/],
  ['expected-value figure', /\b(EV|VE|valor esperado)\s*[:=+-]?\s*\d/i],
  ['betting call to action', /(bet now|apuesta ya|apuesta segura|gana seguro|garantizad[oa]s?|pick del d[ií]a|apuesta de valor)/i],
];

export async function expectNoForbiddenContent(page: Page) {
  const text = await page.locator('body').innerText();
  for (const [label, pattern] of FORBIDDEN_PATTERNS) {
    expect(pattern.test(text), `found ${label}: ${String(text.match(pattern)?.[0])}`).toBe(false);
  }
}
