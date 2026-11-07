import { useId } from 'react';
import { Link } from 'react-router';
import type { Competition, Fixture } from '../../api/schemas.ts';
import { fixturePath } from '../../components/layout/navigation.ts';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useI18n } from '../../i18n/i18n.ts';
import { fixturePhase, fixtureStatusLabel, headlineScore, phaseTone } from './fixtureStatus.ts';
import styles from './Fixtures.module.css';
import { cx } from '../../lib/cx.ts';

export interface FixtureListProps {
  readonly fixtures: readonly Fixture[];
  /** When given, each row names its competition (useful across competitions). */
  readonly competitions?: ReadonlyMap<number, Competition> | undefined;
  /** Query string of the list, so the detail page can link back to the same filters. */
  readonly returnSearch?: string;
  /** Heading level of the day headings. */
  readonly headingLevel?: 3 | 4;
}

function FixtureRow({ fixture, competition, returnSearch }: { fixture: Fixture; competition: Competition | undefined; returnSearch: string }) {
  const { messages: m, format } = useI18n();
  const phase = fixturePhase(fixture.status_short);
  const score = headlineScore(fixture);
  return (
    <li className={styles.row} data-fixture-id={fixture.id}>
      <div className={styles.rowMeta}>
        <time dateTime={fixture.kickoff_at} className={styles.time}>
          {format.time(fixture.kickoff_at)}
        </time>
        {competition ? <span className={styles.metaItem}>{competition.name}</span> : null}
        {fixture.round ? <span className={styles.metaItem}>{fixture.round}</span> : null}
      </div>
      <div className={styles.teams}>
        <Link to={fixturePath(fixture.id)} state={{ from: returnSearch }} className={styles.rowLink}>
          <span className={styles.team}>{fixture.home_team.name}</span>{' '}
          <span className={cx(styles.score, score === null && styles.versus)}>{score ? `${score.home} – ${score.away}` : m.fixtures.versus}</span>{' '}
          <span className={cx(styles.team, styles.away)}>{fixture.away_team.name}</span>
        </Link>
      </div>
      <div className={styles.rowStatus}>
        <StatusBadge tone={phaseTone(phase)}>
          {fixtureStatusLabel(fixture, m)}
          {phase === 'in_play' ? <span className={styles.syncSuffix}> · {m.fixtures.syncedInPlay}</span> : null}
        </StatusBadge>
        <StatusBadge tone="abstain">ABSTAIN</StatusBadge>
      </div>
    </li>
  );
}

function DayGroup({ day, fixtures, props }: { day: string; fixtures: readonly Fixture[]; props: FixtureListProps }) {
  const { messages: m } = useI18n();
  const headingId = useId();
  const Heading = `h${props.headingLevel ?? 3}` as const;
  return (
    <section className={styles.day} aria-labelledby={headingId}>
      <Heading id={headingId} className={styles.dayTitle}>
        {day}
      </Heading>
      <ul className={styles.list} aria-label={m.fixtures.dayLabel(day)}>
        {fixtures.map((fixture) => (
          <FixtureRow
            key={fixture.id}
            fixture={fixture}
            competition={props.competitions?.get(fixture.competition_id)}
            returnSearch={props.returnSearch ?? ''}
          />
        ))}
      </ul>
    </section>
  );
}

/** Fixtures in backend order (kickoff, then id), grouped by calendar day in the display time zone. */
export function FixtureList(props: FixtureListProps) {
  const { format } = useI18n();
  const groups: { key: string; label: string; fixtures: Fixture[] }[] = [];
  for (const fixture of props.fixtures) {
    const key = format.dayKey(fixture.kickoff_at);
    const last = groups.at(-1);
    if (last?.key === key) last.fixtures.push(fixture);
    else groups.push({ key, label: format.day(fixture.kickoff_at), fixtures: [fixture] });
  }
  return (
    <div className={styles.groups}>
      {groups.map((group, index) => (
        <DayGroup key={`${group.key}-${String(index)}`} day={group.label} fixtures={group.fixtures} props={props} />
      ))}
    </div>
  );
}
