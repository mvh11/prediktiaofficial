import { useEffect } from 'react';
import { useMessages } from '../i18n/i18n.ts';

export const SITE_NAME = 'PREDIKTIA';

/** Keeps `<title>` in sync with the current page so screen-reader users hear where they are. */
export function useDocumentTitle(title: string): void {
  const { site } = useMessages();
  useEffect(() => {
    document.title = title === site.name ? site.name : `${title}${site.titleSeparator}${site.name}`;
  }, [title, site]);
}
