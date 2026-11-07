import { useApi } from '../../api/apiContext.ts';
import { describeError } from '../../api/describeError.ts';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useOnlineStatus } from '../../hooks/useOnlineStatus.ts';
import { useResource } from '../../hooks/useResource.ts';
import { useMessages } from '../../i18n/i18n.ts';
import styles from './DataServiceStatus.module.css';

/**
 * Shows whether the PREDIKTIA data service answers GET /health. This only
 * reports reachability of the API process; it says nothing about data quality.
 */
export function DataServiceStatus() {
  const m = useMessages();
  const api = useApi();
  const online = useOnlineStatus();
  const { state } = useResource('health', (signal) => api.getHealth(signal));

  let badge;
  switch (state.status) {
    case 'idle':
    case 'loading':
      badge = <StatusBadge tone="neutral">{m.service.checking}</StatusBadge>;
      break;
    case 'success':
      badge = (
        <StatusBadge tone="success" dot>
          {m.service.connected}
        </StatusBadge>
      );
      break;
    case 'error': {
      const { category } = describeError(state.error, { online });
      const label = category === 'not_configured' ? m.service.notConfigured : category === 'offline' ? m.service.offline : m.service.unavailable;
      badge = (
        <StatusBadge tone="warning" dot>
          {label}
        </StatusBadge>
      );
      break;
    }
  }

  return (
    <p role="status" className={styles.status}>
      <span className="muted">{m.service.label}</span> {badge}
    </p>
  );
}
