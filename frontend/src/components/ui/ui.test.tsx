import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/errors.ts';
import { ApiFailure } from './ApiFailure.tsx';
import { Card } from './Card.tsx';
import { InputField, SelectField } from './Field.tsx';
import { EmptyState, ErrorState, LoadingState } from './States.tsx';
import { StatusBadge } from './StatusBadge.tsx';

describe('state components', () => {
  it('LoadingState is a polite status region with a visible label', () => {
    render(<LoadingState label="Loading fixtures…" />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('Loading fixtures…');
    expect(status).toHaveAttribute('aria-live', 'polite');
  });

  it('EmptyState renders a heading at the requested level and an optional action', () => {
    render(<EmptyState title="Nothing here" headingLevel={2} action={<button type="button">Do it</button>} />);
    expect(screen.getByRole('heading', { level: 2, name: 'Nothing here' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Do it' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('ErrorState is announced as an alert and its retry button is keyboard operable', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <ErrorState title="Failed" onRetry={onRetry}>
        <p>Details</p>
      </ErrorState>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Failed');
    await user.tab();
    expect(screen.getByRole('button', { name: 'Try again' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('ErrorState omits the retry button without a handler', () => {
    render(<ErrorState title="Failed" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('ApiFailure', () => {
  it('offers no retry when the data source is not configured', () => {
    render(<ApiFailure error={new ApiError('not_configured', 'x')} subject="the list" onRetry={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Data source not configured');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('offers a retry for a transient outage', () => {
    render(<ApiFailure error={new ApiError('network', 'x')} subject="the list" onRetry={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Data service unavailable');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('presentational components', () => {
  it('StatusBadge exposes its tone and text', () => {
    render(<StatusBadge tone="abstain">ABSTAIN</StatusBadge>);
    expect(screen.getByText('ABSTAIN')).toHaveAttribute('data-tone', 'abstain');
  });

  it('Card renders title, body and footer with the requested heading level', () => {
    render(
      <Card title="Title" headingLevel={2} footer={<span>Footer</span>}>
        <p>Body</p>
      </Card>,
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Title' })).toBeInTheDocument();
    expect(screen.getByText('Body')).toBeInTheDocument();
    expect(screen.getByText('Footer')).toBeInTheDocument();
  });

  it('fields are labelled and describe hints and errors', () => {
    render(
      <>
        <InputField label="Season" hint="Year" error="Too early" defaultValue="" />
        <SelectField label="Team" hint="Pick one">
          <option value="">All</option>
        </SelectField>
      </>,
    );
    const season = screen.getByLabelText('Season');
    expect(season).toHaveAccessibleDescription('Year Too early');
    expect(season).toHaveAttribute('aria-invalid', 'true');
    const team = screen.getByLabelText('Team');
    expect(team).toHaveAccessibleDescription('Pick one');
    expect(team).not.toHaveAttribute('aria-invalid');
  });
});
