import { Link } from 'react-router';
import type { Fixture } from '../../api/schemas.ts';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { formatKickoff } from '../../lib/format.ts';
import { fixturePhase, fixtureStatusLabel, headlineScore, phaseTone } from './fixtureStatus.ts';
import styles from './Fixtures.module.css';
import { cx } from '../../lib/cx.ts';

function FixtureRow({ fixture }: { fixture: Fixture }) {
  const phase = fixturePhase(fixture.status_short);
  const score = headlineScore(fixture);
  return (
    <li className={styles.row}>
      <div className={styles.rowMeta}>
        <time dateTime={fixture.kickoff_at}>{formatKickoff(fixture.kickoff_at)}</time>
        {fixture.round ? <span className={styles.round}>{fixture.round}</span> : null}
      </div>
      <div className={styles.teams}>
        <Link to={`/fixtures/${fixture.id}`} className={styles.rowLink}>
          <span className={styles.team}>{fixture.home_team.name}</span>
          <span className={styles.score}>{score ? `${score.home} – ${score.away}` : 'vs'}</span>
          <span className={cx(styles.team, styles.away)}>{fixture.away_team.name}</span>
        </Link>
      </div>
      <div className={styles.rowStatus}>
        <StatusBadge tone={phaseTone(phase)}>{fixtureStatusLabel(fixture)}</StatusBadge>
        <StatusBadge tone="abstain">ABSTAIN</StatusBadge>
      </div>
    </li>
  );
}

export function FixtureList({ fixtures, label }: { fixtures: readonly Fixture[]; label: string }) {
  return (
    <ul className={styles.list} aria-label={label}>
      {fixtures.map((fixture) => (
        <FixtureRow key={fixture.id} fixture={fixture} />
      ))}
    </ul>
  );
}
