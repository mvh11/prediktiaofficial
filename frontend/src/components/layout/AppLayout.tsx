import { Suspense } from 'react';
import { Outlet, ScrollRestoration } from 'react-router';
import { LoadingState } from '../ui/States.tsx';
import { SiteFooter } from './SiteFooter.tsx';
import { SiteHeader } from './SiteHeader.tsx';
import styles from './AppLayout.module.css';

export function AppLayout() {
  return (
    <>
      <a className={styles.skipLink} href="#main">
        Skip to main content
      </a>
      <SiteHeader />
      <main id="main" className={styles.main} tabIndex={-1}>
        <Suspense
          fallback={
            <div className="container">
              <div className={styles.pageFallback}>
                <LoadingState label="Loading page…" rows={2} />
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
