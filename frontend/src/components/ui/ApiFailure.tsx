import { describeError } from '../../api/describeError.ts';
import { ErrorState } from './States.tsx';

export interface ApiFailureProps {
  readonly error: unknown;
  /** Lower-case noun phrase used in the message, e.g. "the fixture list". */
  readonly subject: string;
  readonly onRetry?: () => void;
  readonly headingLevel?: 2 | 3;
}

/**
 * Renders any API failure as an honest state. Missing configuration, an
 * unreachable backend, timeouts and malformed responses are "unavailable"
 * (nothing is shown in place of the data); other failures are errors.
 */
export function ApiFailure({ error, subject, onRetry, headingLevel }: ApiFailureProps) {
  const description = describeError(error, subject);
  const variant = description.category === 'rejected' || description.category === 'unexpected' ? 'error' : 'unavailable';
  return (
    <ErrorState
      title={description.title}
      variant={variant}
      headingLevel={headingLevel}
      onRetry={description.retryable ? onRetry : undefined}
    >
      <p>{description.message}</p>
    </ErrorState>
  );
}
