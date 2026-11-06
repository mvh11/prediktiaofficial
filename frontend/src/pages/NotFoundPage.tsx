import { ButtonLink } from '../components/ui/Button.tsx';
import { PageHeader } from '../components/ui/PageHeader.tsx';
import styles from './ContentPage.module.css';
import { cx } from '../lib/cx.ts';

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" eyebrow="Error 404" lead={<p>The page you requested does not exist or has moved.</p>} />
      <div className={cx('container', styles.body)}>
        <div>
          <ButtonLink to="/">Go to the overview</ButtonLink>
        </div>
      </div>
    </>
  );
}
