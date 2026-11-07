import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/errors.ts';
import { ApiFailure } from './ApiFailure.tsx';
import { Breadcrumbs } from './Breadcrumbs.tsx';
import { Card } from './Card.tsx';
import { InputField, SelectField } from './Field.tsx';
import { ProductCodes } from './ProductCodes.tsx';
import { EmptyState, ErrorState, LoadingState } from './States.tsx';
import { StatusBadge } from './StatusBadge.tsx';

afterEach(() => {
  vi.useRealTimers();
});

describe('state components', () => {
  it('LoadingState is a polite status region with a visible label', () => {
    render(<LoadingState label="Cargando partidos…" />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('Cargando partidos…');
    expect(status).toHaveAttribute('aria-live', 'polite');
  });

  it('LoadingState adds a "taking longer than usual" hint to the live region after a delay', () => {
    vi.useFakeTimers();
    render(<LoadingState label="Cargando partidos…" slowAfterMs={3_000} />);
    expect(screen.getByRole('status')).not.toHaveTextContent('Está tardando');
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByRole('status')).toHaveTextContent('Está tardando más de lo habitual');
  });

  it('EmptyState renders a heading at the requested level and an optional action', () => {
    render(<EmptyState title="Nada por aquí" headingLevel={2} action={<button type="button">Hacerlo</button>} />);
    expect(screen.getByRole('heading', { level: 2, name: 'Nada por aquí' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hacerlo' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('ErrorState is announced as an alert and its retry button is keyboard operable', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <ErrorState title="Falló" onRetry={onRetry}>
        <p>Detalle</p>
      </ErrorState>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Falló');
    await user.tab();
    expect(screen.getByRole('button', { name: 'Reintentar' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('ErrorState omits the retry button without a handler', () => {
    render(<ErrorState title="Falló" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('ApiFailure', () => {
  it('offers no retry when the data source is not configured', () => {
    render(<ApiFailure error={new ApiError('not_configured', 'x')} subject="la lista" onRetry={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Fuente de datos no configurada');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('offers a retry for a transient outage', () => {
    render(<ApiFailure error={new ApiError('network', 'x')} subject="la lista" onRetry={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Servicio de datos no disponible');
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
  });

  it('says "offline" when the browser has no connection', () => {
    const onLine = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    render(<ApiFailure error={new ApiError('network', 'x')} subject="la lista" onRetry={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Sin conexión a internet');
    onLine.mockRestore();
  });

  it('explains a rejected page record and keeps extra recovery actions', () => {
    const error = new ApiError('invalid_response', 'x', { invalid: { reason: 'schema', row: 3, field: 'home_team.name' } });
    render(<ApiFailure error={error} subject="la lista de partidos" onRetry={vi.fn()} actions={<button type="button">Ir a la primera página</button>} />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('registro n.º 3');
    expect(alert).toHaveTextContent('«home_team.name»');
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ir a la primera página' })).toBeInTheDocument();
  });
});

describe('presentational components', () => {
  it('StatusBadge exposes its tone and text', () => {
    render(<StatusBadge tone="abstain">ABSTAIN</StatusBadge>);
    expect(screen.getByText('ABSTAIN')).toHaveAttribute('data-tone', 'abstain');
  });

  it('ProductCodes shows the codes verbatim with explanations', () => {
    render(<ProductCodes />);
    for (const code of ['ABSTAIN', 'NOT_CERTIFIED', 'WAIT_REAL_SOURCE', 'RESEARCH_ONLY']) expect(screen.getByText(code)).toBeInTheDocument();
    expect(screen.getByText(/Ningún modelo ni resultado de investigación está certificado/)).toBeInTheDocument();
  });

  it('Breadcrumbs marks only the last crumb as the current page', () => {
    render(
      <MemoryRouter>
        <Breadcrumbs items={[{ label: 'Inicio', to: '/' }, { label: 'Partidos', to: '/partidos' }, { label: 'A vs B' }]} />
      </MemoryRouter>,
    );
    const nav = screen.getByRole('navigation', { name: 'Ruta de navegación' });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Partidos' })).toHaveAttribute('href', '/partidos');
    expect(screen.getByText('A vs B')).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('link', { name: 'A vs B' })).not.toBeInTheDocument();
  });

  it('Card renders title, body and footer with the requested heading level', () => {
    render(
      <Card title="Título" headingLevel={2} footer={<span>Pie</span>}>
        <p>Cuerpo</p>
      </Card>,
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Título' })).toBeInTheDocument();
    expect(screen.getByText('Cuerpo')).toBeInTheDocument();
    expect(screen.getByText('Pie')).toBeInTheDocument();
  });

  it('fields are labelled and describe hints and errors', () => {
    render(
      <>
        <InputField label="Temporada" hint="Año" error="Muy temprano" defaultValue="" />
        <SelectField label="Equipo" hint="Elige uno">
          <option value="">Todos</option>
        </SelectField>
      </>,
    );
    const season = screen.getByLabelText('Temporada');
    expect(season).toHaveAccessibleDescription('Año Muy temprano');
    expect(season).toHaveAttribute('aria-invalid', 'true');
    const team = screen.getByLabelText('Equipo');
    expect(team).toHaveAccessibleDescription('Elige uno');
    expect(team).not.toHaveAttribute('aria-invalid');
  });
});
