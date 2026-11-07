import { useOnlineStatus } from '../../hooks/useOnlineStatus.ts';
import { useMessages } from '../../i18n/i18n.ts';
import styles from './OfflineBanner.module.css';
import { cx } from '../../lib/cx.ts';

/**
 * Always-present polite live region: when the browser goes offline the message
 * is inserted (and announced); it disappears when the connection returns, at
 * which point failed data views retry on their own (see useResource).
 */
export function OfflineBanner() {
  const m = useMessages();
  const online = useOnlineStatus();
  return (
    <div role="status" aria-live="polite" className={styles.region}>
      {online ? null : (
        <div className={styles.banner}>
          <p className={cx('container', styles.text)}>
            <strong>{m.network.offlineTitle}</strong> {m.network.offlineBody}
          </p>
        </div>
      )}
    </div>
  );
}
