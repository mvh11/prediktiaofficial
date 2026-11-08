import { useLayoutEffect } from 'react';
import { useMessages } from '../i18n/i18n.ts';

export const SITE_NAME = 'PREDIKTIA';

/**
 * Keeps `<title>` in sync with the current page so screen-reader users hear where they are.
 *
 * A layout effect runs inside the same commit that inserts the page's heading, so no observer (assistive technology,
 * a MutationObserver, a test) can ever see the new heading next to the previous page's title. With a passive effect
 * the heading was committed first and the title followed in a later task (W2).
 */
export function useDocumentTitle(title: string): void {
  const { site } = useMessages();
  useLayoutEffect(() => {
    document.title = title === site.name ? site.name : `${title}${site.titleSeparator}${site.name}`;
  }, [title, site]);
}
