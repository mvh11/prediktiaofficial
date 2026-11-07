import { PATHS } from '../components/layout/navigation.ts';
import { ButtonLink } from '../components/ui/Button.tsx';
import { PageHeader } from '../components/ui/PageHeader.tsx';
import { useMessages } from '../i18n/i18n.ts';
import styles from './ContentPage.module.css';
import { cx } from '../lib/cx.ts';

export function NotFoundPage() {
  const m = useMessages();
  return (
    <>
      <PageHeader title={m.notFound.title} eyebrow={m.notFound.eyebrow} lead={<p>{m.notFound.lead}</p>} />
      <div className={cx('container', styles.body)}>
        <div className={styles.actions}>
          <ButtonLink to={PATHS.home}>{m.notFound.home}</ButtonLink>
          <ButtonLink to={PATHS.fixtures} variant="secondary">
            {m.notFound.fixtures}
          </ButtonLink>
        </div>
      </div>
    </>
  );
}
