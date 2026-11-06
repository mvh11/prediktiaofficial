import type { BadgeTone } from '../components/ui/StatusBadge.tsx';

/**
 * Single source of truth for what the product may claim about itself.
 *
 * These flags encode the current authorization state (RESEARCH_ONLY,
 * NOT_CERTIFIED, NO_PUBLIC_PREDICTIONS). They are constants on purpose: no
 * runtime switch, environment variable or API response can turn on prediction
 * display in this build. Changing them requires a code change and review.
 */
export const PRODUCT_FLAGS = {
  certifiedPredictionsAuthorized: false,
  publicBettingRecommendations: false,
  verifiedOddsSource: false,
} as const;

export interface Capability {
  readonly id: string;
  readonly name: string;
  readonly status: string;
  readonly tone: BadgeTone;
  readonly description: string;
}

/** Honest platform status, shown on the overview and research pages. */
export const CAPABILITIES: readonly Capability[] = [
  {
    id: 'catalog',
    name: 'Competition & fixture catalog',
    status: 'Read-only',
    tone: 'info',
    description:
      'Competitions, teams and fixtures stored by PREDIKTIA, shown exactly as last synchronized when the data service is connected. Not a live feed.',
  },
  {
    id: 'research',
    name: 'Research models',
    status: 'Research only',
    tone: 'warning',
    description:
      'Goal-distribution models, baselines and calibration diagnostics evaluated on historical data. Their outputs are not published as forecasts.',
  },
  {
    id: 'predictions',
    name: 'Certified predictions',
    status: 'Not available',
    tone: 'abstain',
    description: 'No prediction has been certified or authorized for publication. Every fixture currently resolves to ABSTAIN.',
  },
  {
    id: 'odds',
    name: 'Odds & market data',
    status: 'No verified source',
    tone: 'neutral',
    description: 'There is no verified, authenticated source of bookmaker prices. No odds or expected-value figures are shown.',
  },
  {
    id: 'recommendations',
    name: 'Betting recommendations',
    status: 'Not offered',
    tone: 'neutral',
    description: 'PREDIKTIA does not recommend bets or stakes, and does not offer subscriptions or paid tips.',
  },
];

export interface EvidenceRequirement {
  readonly id: string;
  readonly requirement: string;
  readonly why: string;
  readonly status: 'Not established' | 'Not granted';
}

/**
 * Preconditions for ever publishing a prediction. Their status reflects the
 * project's own documented limitations (docs/modular-data-status.md).
 */
export const EVIDENCE_REQUIREMENTS: readonly EvidenceRequirement[] = [
  {
    id: 'pit',
    requirement: 'Strict point-in-time certification of model inputs',
    why: 'Proves that every input was knowable before kickoff, ruling out look-ahead bias.',
    status: 'Not established',
  },
  {
    id: 'issuance',
    requirement: 'Independently authenticated issuance time',
    why: 'A prediction only counts if it can be proven to exist before the match started.',
    status: 'Not established',
  },
  {
    id: 'emitter',
    requirement: 'Verified prospective model emitter',
    why: 'Forecasts must come from a validated, versioned model running forward in time, not from a backtest.',
    status: 'Not established',
  },
  {
    id: 'odds',
    requirement: 'Authentic provider odds receipts and certified market mappings',
    why: 'Any comparison with market prices needs prices that are genuine, timestamped and correctly mapped.',
    status: 'Not established',
  },
  {
    id: 'ev',
    requirement: 'Certified expected-value methodology',
    why: 'Expected value is meaningless without certified probabilities and authentic prices.',
    status: 'Not established',
  },
  {
    id: 'authorization',
    requirement: 'Authorization to publish predictions',
    why: 'Publication is a separate, explicit decision taken only after all the evidence above exists.',
    status: 'Not granted',
  },
];
