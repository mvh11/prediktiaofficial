import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PRODUCT_FLAGS } from '../content/productStatus.ts';
import { devCompetition, devFinishedFixture, devFixture } from '../test/devOnlyData.ts';
import { jsonResponse, mockFetch } from '../test/fetchMock.ts';
import { renderApp } from '../test/renderApp.tsx';

/**
 * Guards for STRICT PRODUCT TRUTH: this build must never present probabilities,
 * prices, expected value, stakes or recommendations, and synthetic DEV_ONLY
 * data must never be reachable from application code.
 */

const FORBIDDEN_PATTERNS: readonly [string, RegExp][] = [
  ['percentage', /\d\s*%/],
  ['decimal odds or probability', /\b\d+[.,]\d+\b/],
  ['fractional odds', /\b\d+\/\d+\b/],
  ['expected-value figure', /\bEV\s*[:=+-]?\s*\d/i],
  ['stake or unit sizing', /\b(stake|units?)\s*[:=]\s*\d/i],
  ['betting call to action', /\b(bet now|place (a|your) bet|sure (win|bet)|guaranteed|lock of the day|value bet|best bet)\b/i],
  ['testimonial', /\b(testimonial|customers say|5 stars)\b/i],
  ['subscription offer', /\b(subscribe now|premium plan|free trial|pricing)\b/i],
];

function visibleText(): string {
  return document.body.textContent;
}

function expectNoForbiddenContent() {
  const text = visibleText();
  for (const [label, pattern] of FORBIDDEN_PATTERNS) {
    expect(pattern.test(text), `found ${label}: ${String(text.match(pattern)?.[0])}`).toBe(false);
  }
}

describe('product truth', () => {
  it('keeps every publication flag off in this build', () => {
    expect(PRODUCT_FLAGS).toEqual({
      certifiedPredictionsAuthorized: false,
      publicBettingRecommendations: false,
      verifiedOddsSource: false,
    });
  });

  it.each([
    ['/', /built on evidence/i],
    ['/research', 'Quantitative research approach'],
    ['/methodology', 'Methodology and evidence'],
  ])('%s shows no probabilities, odds, EV, stakes or recommendations', async (path, heading) => {
    renderApp(path);
    await screen.findByRole('heading', { level: 1, name: heading });
    expectNoForbiddenContent();
  });

  it('fixture views show only stored facts plus ABSTAIN', async () => {
    mockFetch({
      '/health': () => jsonResponse({ status: 'ok', service: 'prediktia-backend' }),
      '/competitions': () => jsonResponse([devCompetition()]),
      '/competitions/1/teams': () => jsonResponse([]),
      '/fixtures': () => jsonResponse([devFixture(1), devFinishedFixture(2)]),
      '/fixtures/2': () => jsonResponse(devFinishedFixture(2)),
    });
    const { user } = renderApp('/fixtures?competition=1');
    await screen.findByRole('list', { name: 'Fixtures' });
    expectNoForbiddenContent();
    expect(screen.getAllByText('ABSTAIN')).toHaveLength(2);

    await user.click(screen.getByRole('link', { name: /DEV_ONLY Home 2/ }));
    await screen.findByRole('region', { name: 'Model output' });
    expectNoForbiddenContent();
  });

  it('never imports DEV_ONLY test data from application code', () => {
    // Every non-test source module, as raw text (resolved by Vite at test time).
    const sources = import.meta.glob<string>(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}', '!../test/**'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const files = Object.keys(sources);
    expect(files.length).toBeGreaterThan(20);
    const offenders = files.filter((file) => /devOnlyData|DEV_ONLY|\/test\//.test(sources[file] ?? ''));
    expect(offenders).toEqual([]);
  });
});
