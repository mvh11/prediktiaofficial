import { useId } from 'react';
import { Link, useSearchParams } from 'react-router';
import { useApi } from '../../api/apiContext.ts';
import type { Competition } from '../../api/schemas.ts';
import { competitionPath, PATHS } from '../../components/layout/navigation.ts';
import { ApiFailure } from '../../components/ui/ApiFailure.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { InputField } from '../../components/ui/Field.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, LoadingState } from '../../components/ui/States.tsx';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useResource } from '../../hooks/useResource.ts';
import { useI18n } from '../../i18n/i18n.ts';
import { DataServiceStatus } from '../system/DataServiceStatus.tsx';
import { fixturesHref } from '../fixtures/fixtureFilters.ts';
import { competitionType, groupByCountry, matchesCompetition } from './competitionText.ts';
import styles from './Competitions.module.css';
import { cx } from '../../lib/cx.ts';

const SEARCH_PARAM = 'buscar';

export function CompetitionsPage() {
  const { messages: m } = useI18n();
  const api = useApi();
  const competitions = useResource('competitions', (signal) => api.listCompetitions(signal));

  let content;
  switch (competitions.state.status) {
    case 'idle':
    case 'loading':
      content = <LoadingState label={m.competitions.loading} />;
      break;
    case 'error':
      content = <ApiFailure error={competitions.state.error} subject={m.subjects.competitions} onRetry={competitions.reload} headingLevel={2} />;
      break;
    case 'success':
      content =
        competitions.state.data.length === 0 ? (
          <EmptyState title={m.competitions.emptyTitle} headingLevel={2}>
            <p>{m.competitions.empty}</p>
          </EmptyState>
        ) : (
          <CompetitionCatalog competitions={competitions.state.data} />
        );
      break;
  }

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: m.nav.home, to: PATHS.home }, { label: m.nav.competitions }]}
        eyebrow={m.competitions.eyebrow}
        title={m.competitions.title}
        documentTitle={m.competitions.documentTitle}
        lead={<p>{m.competitions.lead}</p>}
        actions={<DataServiceStatus />}
      />
      <div className={cx('container', styles.page)}>{content}</div>
    </>
  );
}

/** Client-side search over the COMPLETE list returned by the backend (no pagination involved). */
function CompetitionCatalog({ competitions }: { competitions: readonly Competition[] }) {
  const { messages: m } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get(SEARCH_PARAM) ?? '';
  const visible = competitions.filter((competition) => matchesCompetition(competition, query));
  const groups = groupByCountry(visible, m);
  const summaryId = useId();

  const setQuery = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value === '') next.delete(SEARCH_PARAM);
    else next.set(SEARCH_PARAM, value);
    setSearchParams(next, { replace: true });
  };

  return (
    <>
      <div className={styles.search} role="search">
        <InputField
          label={m.competitions.searchLabel}
          type="search"
          name={SEARCH_PARAM}
          value={query}
          autoComplete="off"
          hint={m.competitions.searchHint}
          aria-controls={summaryId}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
        />
        <p id={summaryId} className={styles.summary} role="status">
          {m.competitions.searchSummary(visible.length, competitions.length)}
        </p>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          title={m.competitions.noMatchesTitle}
          headingLevel={2}
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setQuery('');
              }}
            >
              {m.competitions.clearSearch}
            </Button>
          }
        >
          <p>{m.competitions.noMatches(query)}</p>
        </EmptyState>
      ) : (
        groups.map((group) => (
          <section key={group.country} className={styles.group} aria-label={m.competitions.listLabel(group.country)}>
            <h2 className={styles.groupTitle}>{group.country}</h2>
            <ul className={styles.grid}>
              {group.competitions.map((competition) => (
                <CompetitionCard key={competition.id} competition={competition} />
              ))}
            </ul>
          </section>
        ))
      )}
    </>
  );
}

function initials(name: string): string {
  const words = name.split(/\s+/).filter((word) => /\p{L}/u.test(word));
  return words
    .slice(0, 2)
    .map((word) => word.charAt(0).toLocaleUpperCase('es-CL'))
    .join('');
}

function CompetitionCard({ competition }: { competition: Competition }) {
  const { messages: m } = useI18n();
  const type = competitionType(competition.type, m);
  return (
    <li className={styles.card}>
      <div className={styles.cardHead}>
        <span className={styles.monogram} aria-hidden="true">
          {initials(competition.name)}
        </span>
        <h3 className={styles.cardTitle}>
          <Link to={competitionPath(competition.id)}>{competition.name}</Link>
        </h3>
      </div>
      <div className={styles.badges}>
        {type ? <StatusBadge tone="info">{type}</StatusBadge> : null}
        <StatusBadge tone={competition.current_season === null ? 'neutral' : 'success'}>
          {competition.current_season === null ? m.competitions.noCurrentSeason : m.competitions.currentSeason(competition.current_season)}
        </StatusBadge>
      </div>
      <p className={styles.cardLinks}>
        <Link to={fixturesHref({ competitionId: competition.id })}>
          {m.competitions.viewFixtures}{' '}
          <span className="visually-hidden">· {competition.name}</span>
        </Link>
      </p>
    </li>
  );
}
