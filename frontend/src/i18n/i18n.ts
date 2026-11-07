import { createContext, use } from 'react';
import { createFormatters, type Formatters } from './format.ts';
import { esCL, type Messages } from './messages.es-CL.ts';

/** Display time zone: continental Chile (Easter Island uses Pacific/Easter). */
export const DISPLAY_TIME_ZONE = 'America/Santiago';

export interface I18n {
  /** BCP 47 tag, also written to <html lang>. */
  readonly locale: string;
  readonly timeZone: string;
  readonly messages: Messages;
  readonly format: Formatters;
}

export function createI18n(locale: string, messages: Messages, timeZone: string = DISPLAY_TIME_ZONE): I18n {
  return { locale, timeZone, messages, format: createFormatters(locale, timeZone) };
}

/** The primary (and currently only) locale. */
export const DEFAULT_I18N: I18n = createI18n('es-CL', esCL);

export const I18nContext = createContext<I18n>(DEFAULT_I18N);

export function useI18n(): I18n {
  return use(I18nContext);
}

/** Shorthand for components that only need text. */
export function useMessages(): Messages {
  return use(I18nContext).messages;
}
