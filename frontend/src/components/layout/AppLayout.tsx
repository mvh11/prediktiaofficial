import { Suspense } from 'react';
import { Outlet, ScrollRestoration } from 'react-router';
import { useMessages } from '../../i18n/i18n.ts';
import { LoadingState } from '../ui/States.tsx';
import { OfflineBanner } from './OfflineBanner.tsx';
import { SiteFooter } from './SiteFooter.tsx';
import { SiteHeader } from './SiteHeader.tsx';
import styles from './AppLayout.module.css';

export function AppLayout() {
  const m = useMessages();
  return (
    <>
      <a className={styles.skipLink} href="#main">
        {m.layout.skipLink}
      </a>
      <SiteHeader />
      <OfflineBanner />
      <main id="main" className={styles.main} tabIndex={-1}>
        <Suspense
          fallback={
            <div className="container">
              <div className={styles.pageFallback}>
                <LoadingState label={m.layout.loadingPage} rows={2} />
              </div>
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </main>
      <SiteFooter />
      <ScrollRestoration />
    </>
  );
}
