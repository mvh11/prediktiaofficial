import { useEffect, useRef, type RefObject } from 'react';
import { useLocation } from 'react-router';

/**
 * Returns a ref for a page's main heading. After a client-side navigation (any
 * location other than the router's initial "default" entry) the heading gets
 * focus on mount, so keyboard and screen-reader users land at the start of the
 * new content. Initial loads and in-page anchor links are left alone, and
 * same-page URL updates (e.g. filters) never move focus because the effect runs
 * only on mount.
 */
export function useNavigationFocus<T extends HTMLElement>(): RefObject<T | null> {
  const ref = useRef<T>(null);
  const location = useLocation();
  const shouldFocus = location.key !== 'default' && location.hash === '';

  useEffect(() => {
    if (shouldFocus) ref.current?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only by design (see above).
  }, []);

  return ref;
}
