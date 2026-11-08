import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { devCompetition, devFinishedFixture } from '../test/devOnlyData.ts';
import { jsonResponse, mockFetch } from '../test/fetchMock.ts';
import { renderApp } from '../test/renderApp.tsx';

/*
 * W2 regression: the page title must change in the same commit as the page heading. Earlier the title was set by a
 * passive effect, one task after the heading was committed, so every observer saw the new heading next to the old
 * title (and a test reading document.title right after findByRole failed under CPU load).
 *
 * The MutationObserver callback runs as a microtask after each commit, before any later task, so it records exactly
 * what assistive technology or a test could observe when the heading first appears.
 */
let observer: MutationObserver | null = null;

afterEach(() => {
  observer?.disconnect();
  observer = null;
});

/** Records document.title at the first DOM mutation where an <h1> with `heading` is present. */
function titleWhenHeadingAppears(heading: string): { readonly title: string | null } {
  const seen: { title: string | null } = { title: null };
  observer = new MutationObserver(() => {
    if (seen.title === null && document.querySelector('h1')?.textContent === heading) seen.title = document.title;
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  return seen;
}

describe('document title (W2)', () => {
  it('is already the fixture title when the fixture heading is first committed', async () => {
    mockFetch({ '/competitions': () => jsonResponse([devCompetition()]), '/fixtures/7': () => jsonResponse(devFinishedFixture(7)) });
    const seen = titleWhenHeadingAppears('DEV_ONLY Home 7 vs DEV_ONLY Away 7');
    renderApp('/partidos/7');
    await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 7 vs DEV_ONLY Away 7' });
    expect(seen.title).toBe('DEV_ONLY Home 7 vs DEV_ONLY Away 7 · PREDIKTIA');
    expect(document.title).toBe('DEV_ONLY Home 7 vs DEV_ONLY Away 7 · PREDIKTIA');
  });

  it('changes together with the heading on client-side navigation, never showing the previous page title', async () => {
    const { user } = renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: /basadas en evidencia/i });
    expect(document.title).toBe('PREDIKTIA');
    const seen = titleWhenHeadingAppears('Metodología y evidencia');
    await user.click(within(screen.getByRole('navigation', { name: 'Principal' })).getByRole('link', { name: 'Metodología' }));
    await screen.findByRole('heading', { level: 1, name: 'Metodología y evidencia' });
    expect(seen.title).toBe('Metodología · PREDIKTIA');
  });

  it('holds across repeated renders (the race was intermittent before)', async () => {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      mockFetch({ '/competitions': () => jsonResponse([devCompetition()]), '/fixtures/7': () => jsonResponse(devFinishedFixture(7)) });
      document.title = '';
      const seen = titleWhenHeadingAppears('DEV_ONLY Home 7 vs DEV_ONLY Away 7');
      const { unmount } = renderApp('/partidos/7');
      await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 7 vs DEV_ONLY Away 7' });
      expect(seen.title, `attempt ${String(attempt + 1)}`).toBe('DEV_ONLY Home 7 vs DEV_ONLY Away 7 · PREDIKTIA');
      observer?.disconnect();
      unmount();
    }
  });
});
