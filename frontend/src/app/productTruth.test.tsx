import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PRODUCT_FLAGS } from '../content/productStatus.ts';
import { esCL } from '../i18n/messages.es-CL.ts';
import { devCompetition, devFinishedFixture, devFixture, devTeam } from '../test/devOnlyData.ts';
import { jsonResponse, mockFetch } from '../test/fetchMock.ts';
import { renderApp } from '../test/renderApp.tsx';

/**
 * Guards for STRICT PRODUCT TRUTH: this build must never present probabilities,
 * prices, expected value, stakes or recommendations (in Spanish or English),
 * and synthetic DEV_ONLY data must never be reachable from application code.
 */

const FORBIDDEN_PATTERNS: readonly [string, RegExp][] = [
  ['percentage', /\d\s*%/],
  ['decimal odds or probability', /\b\d+[.,]\d+\b/],
  ['fractional odds', /\b\d+\/\d+\b/],
  ['expected-value figure', /\b(EV|VE|valor esperado)\s*[:=+-]?\s*\d/i],
  ['stake or unit sizing', /\b(stake|units?|monto|unidades?)\s*[:=]\s*\d/i],
  ['betting call to action (en)', /\b(bet now|place (a|your) bet|sure (win|bet)|guaranteed|lock of the day|value bet|best bet)\b/i],
  ['betting call to action (es)', /(apuesta ya|apuesta segura|gana seguro|ganancia segura|garantizad[oa]s?|pick del d[ií]a|apuesta de valor|mejor apuesta)/i],
  ['testimonial', /\b(testimonial|customers say|5 stars|testimonios?|nuestros clientes|5 estrellas)\b/i],
  ['subscription offer', /\b(subscribe now|premium plan|free trial|pricing|suscr[ií]bete|plan premium|prueba gratis)\b/i],
];

function expectNoForbiddenContent() {
  const text = document.body.textContent;
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
    ['/', /basadas en evidencia/i],
    ['/investigacion', 'Enfoque de investigación cuantitativa'],
    ['/metodologia', 'Metodología y evidencia'],
  ])('%s shows no probabilities, odds, EV, stakes or recommendations', async (path, heading) => {
    renderApp(path, { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: heading });
    expectNoForbiddenContent();
  });

  it('the home and research pages state the product status codes verbatim', async () => {
    renderApp('/investigacion', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: 'Enfoque de investigación cuantitativa' });
    for (const code of ['ABSTAIN', 'NOT_CERTIFIED', 'WAIT_REAL_SOURCE', 'RESEARCH_ONLY']) expect(screen.getAllByText(code).length).toBeGreaterThan(0);
  });

  it('catalog and fixture views show only stored facts plus ABSTAIN', async () => {
    mockFetch({
      '/health': () => jsonResponse({ status: 'ok', service: 'prediktia-backend' }),
      '/competitions': () => jsonResponse([devCompetition()]),
      '/competitions/1/teams': () => jsonResponse([devTeam(11)]),
      '/fixtures': () => jsonResponse([devFixture(1), devFinishedFixture(2)]),
      '/fixtures/2': () => jsonResponse(devFinishedFixture(2)),
    });
    const { user } = renderApp('/competiciones/1');
    await screen.findByRole('list', { name: 'Equipos' });
    expectNoForbiddenContent();

    await user.click(screen.getByRole('link', { name: 'Ver partidos de la temporada 2030' }));
    await screen.findByText(/^Partidos 1 a 2/);
    expectNoForbiddenContent();
    expect(screen.getAllByText('ABSTAIN')).toHaveLength(2);

    await user.click(screen.getByRole('link', { name: /DEV_ONLY Home 2/ }));
    await screen.findByRole('region', { name: 'Salida del modelo' });
    expectNoForbiddenContent();
  });

  it('no catalog text promises predictions or suggests betting', () => {
    const text = JSON.stringify(esCL, (_key, value: unknown) => (typeof value === 'function' ? String(value) : value));
    for (const [label, pattern] of FORBIDDEN_PATTERNS.slice(4)) {
      expect(pattern.test(text), `catalog contains ${label}: ${String(text.match(pattern)?.[0])}`).toBe(false);
    }
  });

  it('never imports DEV_ONLY test data from application code', () => {
    // Every non-test source module, as raw text (resolved by Vite at test time).
    const sources = import.meta.glob<string>(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}', '!../test/**'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const files = Object.keys(sources);
    expect(files.length).toBeGreaterThan(40);
    const offenders = files.filter((file) => /devOnlyData|DEV_ONLY|\/test\//.test(sources[file] ?? ''));
    expect(offenders).toEqual([]);
  });

  it('only issues GET requests to the five verified endpoints', () => {
    const sources = import.meta.glob<string>(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}', '!../test/**'], {
      query: '?raw',
      import: 'default',
      eager: true,
    });
    const all = Object.values(sources).join('\n');
    expect(all).not.toMatch(/method:\s*['"`](POST|PUT|PATCH|DELETE)/);
    const paths = new Set([...all.matchAll(/getJson\(\s*[`'"]([^`'"]+)[`'"]/g)].map((match) => match[1]));
    expect([...paths].sort()).toEqual(['/competitions', '/competitions/${competitionId}/teams', '/fixtures', '/fixtures/${fixtureId}', '/health']);
  });
});
