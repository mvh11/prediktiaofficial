import { useEffect, type ReactNode } from 'react';
import { DEFAULT_I18N, I18nContext, type I18n } from './i18n.ts';

/** Provides texts and formatters, and keeps <html lang> in sync with the active locale. */
export function I18nProvider({ value = DEFAULT_I18N, children }: { value?: I18n; children: ReactNode }) {
  useEffect(() => {
    document.documentElement.lang = value.locale;
  }, [value.locale]);
  return <I18nContext value={value}>{children}</I18nContext>;
}
