import { isRouteErrorResponse, useRouteError } from 'react-router';
import { useDocumentTitle } from '../hooks/useDocumentTitle.ts';
import { ErrorState } from '../components/ui/States.tsx';

/**
 * Last-resort boundary for unexpected render errors. Deliberately standalone
 * (no layout/context dependencies) so it can render even if those failed.
 */
export function RouteErrorPage() {
  const error = useRouteError();
  useDocumentTitle('Something went wrong');
  const message = isRouteErrorResponse(error)
    ? `The page could not be loaded (HTTP ${error.status}).`
    : 'An unexpected error occurred while displaying this page.';
  return (
    <main id="main" className="container" style={{ paddingBlock: 'var(--space-7)' }}>
      <h1>Something went wrong</h1>
      <ErrorState
        title="This page failed to load"
        onRetry={() => {
          window.location.reload();
        }}
        retryLabel="Reload the page"
        headingLevel={2}
      >
        <p>{message}</p>
        <p>
          <a href="/">Return to the overview</a>
        </p>
      </ErrorState>
    </main>
  );
}
