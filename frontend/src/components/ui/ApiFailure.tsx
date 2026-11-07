import type { ReactNode } from 'react';
import { describeError } from '../../api/describeError.ts';
import { useOnlineStatus } from '../../hooks/useOnlineStatus.ts';
import { useMessages } from '../../i18n/i18n.ts';
import { failureText } from '../../i18n/failureText.ts';
import { ErrorState } from './States.tsx';

export interface ApiFailureProps {
  readonly error: unknown;
  /** Noun phrase with article used in the message, e.g. "la lista de partidos" (see messages.subjects). */
  readonly subject: string;
  readonly onRetry?: () => void;
  /** Extra recovery actions shown next to the retry button. */
  readonly actions?: ReactNode;
  readonly headingLevel?: 2 | 3;
}

/**
 * Renders any API failure as an honest state. Missing configuration, an
 * unreachable backend, being offline, timeouts and malformed responses are
 * "unavailable" (nothing is shown in place of the data); other failures are errors.
 */
export function ApiFailure({ error, subject, onRetry, actions, headingLevel }: ApiFailureProps) {
  const m = useMessages();
  const online = useOnlineStatus();
  const failure = describeError(error, { online });
  const text = failureText(failure, subject, m);
  const variant = failure.category === 'rejected' || failure.category === 'unexpected' ? 'error' : 'unavailable';
  return (
    <ErrorState
      title={text.title}
      variant={variant}
      headingLevel={headingLevel}
      onRetry={failure.retryable ? onRetry : undefined}
      actions={actions}
    >
      {text.paragraphs.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
    </ErrorState>
  );
}
