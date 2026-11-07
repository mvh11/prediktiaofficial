import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderApp } from '../test/renderApp.tsx';

const HOME_HEADING = /basadas en evidencia/i;

function primaryNav() {
  return screen.getByRole('navigation', { name: 'Principal' });
}

describe('navigation (es-CL)', () => {
  it('renders the home page on "/" with Spanish landmarks, language and a skip link', async () => {
    renderApp('/');
    expect(await screen.findByRole('heading', { level: 1, name: HOME_HEADING })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('es-CL');
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main');
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Saltar al contenido principal' })).toHaveAttribute('href', '#main');
    expect(document.title).toBe('PREDIKTIA');
    expect(within(primaryNav()).getByRole('link', { name: 'Inicio' })).toHaveAttribute('aria-current', 'page');
    expect(within(primaryNav()).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Inicio',
      'Competiciones',
      'Partidos',
      'Investigación',
      'Metodología',
    ]);
  });

  it('does not steal focus on the initial page load', async () => {
    renderApp('/investigacion');
    await screen.findByRole('heading', { level: 1, name: 'Enfoque de investigación cuantitativa' });
    expect(document.body).toHaveFocus();
  });

  it.each([
    ['Competiciones', 'Competiciones', '/competiciones', 'Competiciones'],
    ['Partidos', 'Partidos', '/partidos', 'Partidos'],
    ['Investigación', 'Enfoque de investigación cuantitativa', '/investigacion', 'Investigación'],
    ['Metodología', 'Metodología y evidencia', '/metodologia', 'Metodología'],
  ])('navigates to %s, updates title, aria-current, breadcrumbs and focus', async (label, heading, path, documentTitle) => {
    const { user, router } = renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: HOME_HEADING });

    await user.click(within(primaryNav()).getByRole('link', { name: label }));

    const h1 = await screen.findByRole('heading', { level: 1, name: heading });
    expect(router.state.location.pathname).toBe(path);
    expect(within(primaryNav()).getByRole('link', { name: label })).toHaveAttribute('aria-current', 'page');
    expect(within(primaryNav()).getByRole('link', { name: 'Inicio' })).not.toHaveAttribute('aria-current');
    await waitFor(() => {
      expect(h1).toHaveFocus();
    });
    expect(document.title).toBe(`${documentTitle} · PREDIKTIA`);
    const crumbs = screen.getByRole('navigation', { name: 'Ruta de navegación' });
    expect(within(crumbs).getByRole('link', { name: 'Inicio' })).toHaveAttribute('href', '/');
  });

  it('is fully keyboard navigable: Tab reaches the skip link first, then the brand link', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: HOME_HEADING });
    await user.tab();
    expect(screen.getByRole('link', { name: 'Saltar al contenido principal' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('link', { name: 'PREDIKTIA: ir al inicio' })).toHaveFocus();
  });

  it('navigates with the keyboard (Enter on a nav link)', async () => {
    const { user, router } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: HOME_HEADING });
    within(primaryNav()).getByRole('link', { name: 'Metodología' }).focus();
    await user.keyboard('{Enter}');
    await screen.findByRole('heading', { level: 1, name: 'Metodología y evidencia' });
    expect(router.state.location.pathname).toBe('/metodologia');
  });

  it('shows a 404 page for unknown routes', async () => {
    renderApp('/no-existe');
    expect(await screen.findByRole('heading', { level: 1, name: 'Página no encontrada' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ir al inicio' })).toHaveAttribute('href', '/');
  });

  it('home calls to action follow the journey Inicio → Competiciones → Partidos → Investigación', async () => {
    const { user, router } = renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: HOME_HEADING });
    const journey = screen.getByRole('heading', { level: 2, name: 'Cómo recorrer PREDIKTIA' }).closest('section') as HTMLElement;
    expect(within(journey).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
      'Competiciones',
      'Partidos',
      'Detalle del partido',
      'Contexto de investigación',
    ]);
    await user.click(screen.getByRole('link', { name: 'Explorar competiciones' }));
    await screen.findByRole('heading', { level: 1, name: 'Competiciones' });
    expect(router.state.location.pathname).toBe('/competiciones');
  });

  it('methodology table of contents links to in-page sections', async () => {
    renderApp('/metodologia');
    const toc = await screen.findByRole('navigation', { name: 'En esta página' });
    const links = within(toc).getAllByRole('link');
    expect(links).toHaveLength(7);
    for (const link of links) {
      const target = link.getAttribute('href')?.slice(1) ?? '';
      expect(document.getElementById(target)).not.toBeNull();
    }
  });
});

describe('legacy (Phase 1) URLs', () => {
  it.each([
    ['/fixtures?competition=1&status=finished&page=2', '/partidos', '?competicion=1&estado=finalizados&pagina=2'],
    ['/fixtures/12', '/partidos/12', ''],
    ['/research', '/investigacion', ''],
    ['/methodology', '/metodologia', ''],
  ])('%s redirects to the Spanish URL', async (from, pathname, search) => {
    const { router } = renderApp(from, { baseUrl: null });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(pathname);
    });
    expect(router.state.location.search).toBe(search);
  });

  it('keeps legacy methodology anchors working', async () => {
    const { router } = renderApp('/methodology#abstain');
    await waitFor(() => {
      expect(router.state.location.hash).toBe('#abstencion');
    });
    expect(router.state.location.pathname).toBe('/metodologia');
  });
});

describe('mobile navigation', () => {
  it('toggles with the keyboard, exposes state, and closes on Escape returning focus', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: HOME_HEADING });
    const toggle = screen.getByRole('button', { name: 'Menú' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    const panelId = toggle.getAttribute('aria-controls') ?? '';
    const panel = document.getElementById(panelId);
    expect(panel).not.toBeNull();
    expect(panel).not.toBeVisible();

    toggle.focus();
    await user.keyboard('{Enter}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAccessibleName('Cerrar menú');
    expect(screen.getByRole('navigation', { name: 'Menú móvil' })).toBeVisible();

    await user.tab();
    expect(within(screen.getByRole('navigation', { name: 'Menú móvil' })).getByRole('link', { name: 'Inicio' })).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('toggles with Space and closes after choosing a destination', async () => {
    const { user, router } = renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: HOME_HEADING });
    screen.getByRole('button', { name: 'Menú' }).focus();
    await user.keyboard(' ');
    const mobile = screen.getByRole('navigation', { name: 'Menú móvil' });
    await user.click(within(mobile).getByRole('link', { name: 'Investigación' }));
    await screen.findByRole('heading', { level: 1, name: 'Enfoque de investigación cuantitativa' });
    expect(router.state.location.pathname).toBe('/investigacion');
    expect(screen.getByRole('button', { name: 'Menú' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes when pressing outside the menu', async () => {
    const { user } = renderApp('/');
    await screen.findByRole('heading', { level: 1, name: HOME_HEADING });
    await user.click(screen.getByRole('button', { name: 'Menú' }));
    expect(screen.getByRole('button', { name: 'Cerrar menú' })).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('heading', { level: 1 }));
    expect(screen.getByRole('button', { name: 'Menú' })).toHaveAttribute('aria-expanded', 'false');
  });
});
