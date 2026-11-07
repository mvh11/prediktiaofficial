import { isRouteErrorResponse, useRouteError } from 'react-router';
import { useDocumentTitle } from '../hooks/useDocumentTitle.ts';
import { ErrorState } from '../components/ui/States.tsx';
import { useMessages } from '../i18n/i18n.ts';

/**
 * Last-resort boundary for unexpected render errors. Deliberately standalone
 * (no layout or API dependencies) so it can render even if those failed.
 */
export function RouteErrorPage() {
  const m = useMessages();
  const error = useRouteError();
  useDocumentTitle(m.routeError.documentTitle);
  const message = isRouteErrorResponse(error) ? m.routeError.httpStatus(error.status) : m.routeError.generic;
  return (
    <main id="main" className="container" style={{ paddingBlock: 'var(--space-7)' }}>
      <h1>{m.routeError.title}</h1>
      <ErrorState
        title={m.routeError.stateTitle}
        onRetry={() => {
          window.location.reload();
        }}
        retryLabel={m.routeError.reload}
        headingLevel={2}
      >
        <p>{message}</p>
        <p>
          <a href="/">{m.routeError.home}</a>
        </p>
      </ErrorState>
    </main>
  );
}
