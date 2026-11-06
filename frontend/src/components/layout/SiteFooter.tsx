import { Link } from 'react-router';
import { BrandMark } from './BrandMark.tsx';
import { NAV_ITEMS } from './navigation.ts';
import styles from './SiteFooter.module.css';
import { cx } from '../../lib/cx.ts';

export function SiteFooter() {
  return (
    <footer className={styles.footer}>
      <div className={cx('container', styles.inner)}>
        <div className={styles.about}>
          <BrandMark />
          <p className={styles.disclaimer}>
            Research preview. PREDIKTIA does not publish certified predictions, odds, expected-value figures or betting
            recommendations. Nothing on this site is betting or financial advice.
          </p>
        </div>
        <nav aria-label="Footer">
          <ul className={styles.links}>
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <Link to={item.to}>{item.label}</Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </footer>
  );
}
