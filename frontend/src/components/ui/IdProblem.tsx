import { useI18n } from '../../i18n/i18n.ts';
import { MAX_DB_INT, type ParsedId } from '../../lib/ids.ts';
import { ButtonLink } from './Button.tsx';
import type { Crumb } from './Breadcrumbs.tsx';
import { PageHeader } from './PageHeader.tsx';
import { EmptyState } from './States.tsx';

export interface IdProblemProps {
  readonly id: Exclude<ParsedId, { kind: 'valid' }>;
  readonly eyebrow: string;
  readonly breadcrumbs: readonly Crumb[];
  readonly backTo: string;
  readonly backLabel: string;
}

/**
 * Shown instead of a detail page when the URL id is not usable. No request is
 * made: an invalid id cannot exist, and an out-of-range id is reported as such
 * rather than being rounded or sent to the backend.
 */
export function IdProblem({ id, eyebrow, breadcrumbs, backTo, backLabel }: IdProblemProps) {
  const { messages: m, format } = useI18n();
  const unsupported = id.kind === 'unsupported';
  const title = unsupported ? m.ids.unsupportedTitle : m.ids.invalidTitle;
  return (
    <>
      <PageHeader title={title} eyebrow={eyebrow} breadcrumbs={[...breadcrumbs, { label: title }]} />
      <div className="container" style={{ paddingBlock: 'var(--space-6) var(--space-8)' }}>
        <EmptyState title={title} headingLevel={2} action={<ButtonLink to={backTo}>{backLabel}</ButtonLink>}>
          <p>{unsupported ? m.ids.unsupported(id.raw, format.integer(MAX_DB_INT)) : m.ids.invalid(id.raw)}</p>
        </EmptyState>
      </div>
    </>
  );
}
