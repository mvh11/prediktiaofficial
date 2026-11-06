import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { devFinishedFixture, devFixture } from '../../test/devOnlyData.ts';
import { jsonResponse, mockFetch, networkFailure } from '../../test/fetchMock.ts';
import { renderApp } from '../../test/renderApp.tsx';

describe('FixtureDetailPage', () => {
  it('shows stored details, all complete score pairs and the ABSTAIN model output', async () => {
    mockFetch({ '/fixtures/7': () => jsonResponse(devFinishedFixture(7, { status_short: 'AET', home_goals: 3, away_goals: 2, extratime_home: 1, extratime_away: 1, fulltime_home: 2, fulltime_away: 1, venue_name: 'DEV_ONLY Ground' })) });
    renderApp('/fixtures/7');

    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 7 vs DEV_ONLY Away 7' })).toBeInTheDocument();
    expect(screen.getByText('After extra time')).toBeInTheDocument();

    const table = screen.getByRole('table', { name: /Recorded scores/ });
    const rows = within(table).getAllByRole('row').slice(1).map((row) => row.textContent);
    expect(rows).toEqual(['Final score32', '90 minutes21', 'Half-time10', 'Extra time11']);

    expect(screen.getByText('DEV_ONLY Ground')).toBeInTheDocument();
    expect(screen.getAllByText('Not recorded').length).toBeGreaterThan(0);

    const output = screen.getByRole('region', { name: 'Model output' });
    expect(within(output).getByText('ABSTAIN')).toBeInTheDocument();
    expect(within(output).getByRole('link', { name: 'Why PREDIKTIA abstains' })).toHaveAttribute('href', '/methodology#abstain');
  });

  it('says so when no score is recorded, and warns that in-play data is not live', async () => {
    mockFetch({ '/fixtures/8': () => jsonResponse(devFixture(8, { status_short: '2H', elapsed: 60 })) });
    renderApp('/fixtures/8');
    expect(await screen.findByText('No score is recorded for this fixture.')).toBeInTheDocument();
    expect(screen.getByText(/This is not a live score\./)).toBeInTheDocument();
  });

  it('shows a not-found state for a 404', async () => {
    mockFetch({ '/fixtures/404': () => jsonResponse({ detail: 'Partido no encontrado' }, 404) });
    renderApp('/fixtures/404');
    expect(await screen.findByRole('heading', { level: 1, name: 'Fixture not found' })).toBeInTheDocument();
  });

  it('does not request invalid ids', async () => {
    const fetchMock = mockFetch({});
    renderApp('/fixtures/abc');
    expect(await screen.findByRole('heading', { level: 1, name: 'Fixture not found' })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows an unavailable state with retry when the backend is down', async () => {
    let calls = 0;
    mockFetch({
      '/fixtures/9': () => {
        calls += 1;
        return calls === 1 ? networkFailure() : jsonResponse(devFixture(9));
      },
    });
    const { user } = renderApp('/fixtures/9');
    expect(await screen.findByRole('heading', { name: 'Data service unavailable' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '← Back to fixtures' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 9 vs DEV_ONLY Away 9' })).toBeInTheDocument();
  });
});
