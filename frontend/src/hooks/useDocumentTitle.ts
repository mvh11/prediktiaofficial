import { useEffect } from 'react';

export const SITE_NAME = 'PREDIKTIA';

/** Keeps `<title>` in sync with the current page so screen-reader users hear where they are. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title === SITE_NAME ? SITE_NAME : `${title} · ${SITE_NAME}`;
  }, [title]);
}
