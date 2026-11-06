import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderApp } from '../test/renderApp.tsx';

function primaryNav() {
  return screen.getByRole('navigation', { name: 'Primary' });
}

describe('navigation', () => {
  it('renders the overview on "/" with landmarks and a skip link', async () => {
    renderApp('/');
    expect(await screen.findByRole('heading', { level: 1, name: /built on evidence/i })).toBeInTheDocument();
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main');
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveAttribute('href', '#main');
    expect(document.title).toBe('PREDIKTIA');
    expect(within(primaryNav()).getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
  });

  it('does not steal focus on the initial page load', async () => {
    renderApp('/research');
    await screen.findByRole('heading', { level: 1, name: 'Quantitative research approach' });
    expect(document.body).toHaveFocus();
  });

  it.each([
    ['Research', 'Quantitative research approach', '/research'],
    ['Methodology', 'Methodology and evidence', '/methodology'],
    ['Fixtures', 'Fixture browser', '/fixtures'],
  ])('navigates to %s, updates title, aria-current and focus', async (label, heading, path) => {
    const { user, router } = renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: /built on evidence/i });

    await user.click(within(primaryNav()).getByRole('link', { name: label }));

    const h1 = await screen.findByRole('heading', { level: 1, name: heading });
    expect(router.state.location.pathname).toBe(path);
    expect(within(primaryNav()).getByRole('link', { name: label })).toHaveAttribute('aria-current', 'page');
    expect(within(primaryNav()).getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current');
    await waitFor(() => {
      expect(h1).toHaveFocus();
    });
    expect(document.title).toBe(`${heading} · PREDIKTIA`);
  });

  it('is fully keyboard navigable: Tab reaches the skip link first, then the nav', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: /built on evidence/i });
    await user.tab();
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('link', { name: 'PREDIKTIA home' })).toHaveFocus();
  });

  it('navigates with the keyboard (Enter on a nav link)', async () => {
    const { user, router } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: /built on evidence/i });
    within(primaryNav()).getByRole('link', { name: 'Methodology' }).focus();
    await user.keyboard('{Enter}');
    await screen.findByRole('heading', { level: 1, name: 'Methodology and evidence' });
    expect(router.state.location.pathname).toBe('/methodology');
  });

  it('shows a 404 page for unknown routes', async () => {
    renderApp('/does-not-exist');
    expect(await screen.findByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to the overview' })).toHaveAttribute('href', '/');
  });

  it('home calls to action lead to the right pages', async () => {
    const { user, router } = renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: /built on evidence/i });
    await user.click(screen.getByRole('link', { name: 'Read the methodology' }));
    await screen.findByRole('heading', { level: 1, name: 'Methodology and evidence' });
    expect(router.state.location.pathname).toBe('/methodology');
  });

  it('methodology table of contents links to in-page sections', async () => {
    renderApp('/methodology');
    const toc = await screen.findByRole('navigation', { name: 'On this page' });
    for (const link of within(toc).getAllByRole('link')) {
      const target = link.getAttribute('href')?.slice(1) ?? '';
      expect(document.getElementById(target)).not.toBeNull();
    }
  });
});

describe('mobile navigation', () => {
  it('toggles with the keyboard, exposes state, and closes on Escape returning focus', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: /built on evidence/i });
    const toggle = screen.getByRole('button', { name: 'Menu' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    const panelId = toggle.getAttribute('aria-controls') ?? '';
    const panel = document.getElementById(panelId);
    expect(panel).not.toBeNull();
    expect(panel).not.toBeVisible();

    toggle.focus();
    await user.keyboard('{Enter}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAccessibleName('Close menu');
    expect(screen.getByRole('navigation', { name: 'Mobile' })).toBeVisible();

    await user.tab();
    expect(within(screen.getByRole('navigation', { name: 'Mobile' })).getByRole('link', { name: 'Overview' })).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('toggles with Space and closes after choosing a destination', async () => {
    const { user, router } = renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: /built on evidence/i });
    screen.getByRole('button', { name: 'Menu' }).focus();
    await user.keyboard(' ');
    const mobile = screen.getByRole('navigation', { name: 'Mobile' });
    await user.click(within(mobile).getByRole('link', { name: 'Research' }));
    await screen.findByRole('heading', { level: 1, name: 'Quantitative research approach' });
    expect(router.state.location.pathname).toBe('/research');
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes when pressing outside the menu', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: /built on evidence/i });
    await user.click(screen.getByRole('button', { name: 'Menu' }));
    expect(screen.getByRole('button', { name: 'Close menu' })).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('heading', { level: 1 }));
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveAttribute('aria-expanded', 'false');
  });
});
