import type { BadgeTone } from '../components/ui/StatusBadge.tsx';
import type { Messages } from '../i18n/messages.es-CL.ts';

/**
 * Single source of truth for what the product may claim about itself.
 *
 * These flags encode the current authorization state (RESEARCH_ONLY,
 * NOT_CERTIFIED, WAIT_REAL_SOURCE, NO_PUBLIC_PREDICTIONS). They are constants
 * on purpose: no runtime switch, environment variable or API response can turn
 * on prediction display in this build. Changing them requires a code change and
 * review. Texts live in the i18n catalogs, keyed by the ids below.
 */
export const PRODUCT_FLAGS = {
  certifiedPredictionsAuthorized: false,
  publicBettingRecommendations: false,
  verifiedOddsSource: false,
} as const;

/** Machine-readable status codes shown verbatim (with a localized explanation). */
export const PRODUCT_CODES = ['ABSTAIN', 'NOT_CERTIFIED', 'WAIT_REAL_SOURCE', 'RESEARCH_ONLY'] as const;
export type ProductCode = (typeof PRODUCT_CODES)[number];

export const PRODUCT_CODE_TONES: Readonly<Record<ProductCode, BadgeTone>> = {
  ABSTAIN: 'abstain',
  NOT_CERTIFIED: 'warning',
  WAIT_REAL_SOURCE: 'neutral',
  RESEARCH_ONLY: 'info',
};

export type CapabilityId = keyof Messages['capabilities'];

/** Honest platform status, shown on the home and research pages (order matters). */
export const CAPABILITIES: readonly { readonly id: CapabilityId; readonly tone: BadgeTone }[] = [
  { id: 'catalog', tone: 'info' },
  { id: 'research', tone: 'warning' },
  { id: 'predictions', tone: 'abstain' },
  { id: 'odds', tone: 'neutral' },
  { id: 'recommendations', tone: 'neutral' },
];

export type EvidenceId = keyof Messages['evidence']['items'];

/**
 * Preconditions for ever publishing a prediction. Their status reflects the
 * project's own documented limitations (docs/modular-data-status.md).
 */
export const EVIDENCE_REQUIREMENTS: readonly { readonly id: EvidenceId; readonly status: 'not_established' | 'not_granted' }[] = [
  { id: 'pit', status: 'not_established' },
  { id: 'issuance', status: 'not_established' },
  { id: 'emitter', status: 'not_established' },
  { id: 'odds', status: 'not_established' },
  { id: 'ev', status: 'not_established' },
  { id: 'authorization', status: 'not_granted' },
];
