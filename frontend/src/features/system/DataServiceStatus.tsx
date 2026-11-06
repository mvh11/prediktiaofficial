import { useApi } from '../../api/apiContext.ts';
import { describeError } from '../../api/describeError.ts';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useResource } from '../../hooks/useResource.ts';

/**
 * Shows whether the PREDIKTIA data service answers GET /health. This only
 * reports reachability of the API process; it says nothing about data quality.
 */
export function DataServiceStatus() {
  const api = useApi();
  const { state } = useResource('health', (signal) => api.getHealth(signal));

  let badge;
  switch (state.status) {
    case 'idle':
    case 'loading':
      badge = <StatusBadge tone="neutral">Checking…</StatusBadge>;
      break;
    case 'success':
      badge = (
        <StatusBadge tone="success" dot>
          Connected
        </StatusBadge>
      );
      break;
    case 'error': {
      const { category } = describeError(state.error);
      badge = (
        <StatusBadge tone="warning" dot>
          {category === 'not_configured' ? 'Not configured' : 'Unavailable'}
        </StatusBadge>
      );
      break;
    }
  }

  return (
    <p role="status" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', margin: 0 }}>
      <span className="muted">Data service:</span> {badge}
    </p>
  );
}
