import { act, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { THEME_STORAGE_KEY } from '../../hooks/useTheme.ts';
import { renderApp } from '../../test/renderApp.tsx';

afterEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe('theme switcher', () => {
  it('follows the system by default and persists an explicit choice', async () => {
    const { user } = renderApp('/', { baseUrl: null });
    const group = await screen.findByRole('group', { name: 'Apariencia' });
    expect(within(group).getByRole('radio', { name: 'Según el sistema' })).toBeChecked();
    expect(document.documentElement.dataset.theme).toBeUndefined();

    await user.click(within(group).getByRole('radio', { name: 'Oscura' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');

    await user.click(within(group).getByRole('radio', { name: 'Según el sistema' }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it('restores a saved choice', async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light');
    renderApp('/', { baseUrl: null });
    const group = await screen.findByRole('group', { name: 'Apariencia' });
    expect(within(group).getByRole('radio', { name: 'Clara' })).toBeChecked();
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});

describe('offline banner', () => {
  it('announces loss of connection and clears it when the connection returns', async () => {
    renderApp('/', { baseUrl: null });
    await screen.findByRole('heading', { level: 1 });
    expect(screen.queryByText('Sin conexión a internet.')).not.toBeInTheDocument();

    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    act(() => {
      window.dispatchEvent(new Event('offline'));
    });
    expect(screen.getByText('Sin conexión a internet.')).toBeInTheDocument();
    expect(screen.getByText('Sin conexión a internet.').closest('[role="status"]')).toHaveAttribute('aria-live', 'polite');

    onLine.mockReturnValue(true);
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    expect(screen.queryByText('Sin conexión a internet.')).not.toBeInTheDocument();
  });
});

describe('footer', () => {
  it('states the research-only disclaimer and that data is not live', async () => {
    renderApp('/', { baseUrl: null });
    const footer = await screen.findByRole('contentinfo');
    expect(footer).toHaveTextContent('Nada en este sitio constituye asesoría de apuestas ni financiera.');
    expect(footer).toHaveTextContent('No es un servicio en vivo.');
    expect(within(footer).getByRole('navigation', { name: 'Enlaces del pie de página' })).toBeInTheDocument();
  });
});
