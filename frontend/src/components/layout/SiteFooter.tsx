import { Link } from 'react-router';
import { useMessages } from '../../i18n/i18n.ts';
import { BrandMark } from './BrandMark.tsx';
import { NAV_ITEMS } from './navigation.ts';
import { ThemeSwitcher } from './ThemeSwitcher.tsx';
import styles from './SiteFooter.module.css';
import { cx } from '../../lib/cx.ts';

export function SiteFooter() {
  const m = useMessages();
  return (
    <footer className={styles.footer}>
      <div className={cx('container', styles.inner)}>
        <div className={styles.about}>
          <BrandMark />
          <p className={styles.disclaimer}>{m.layout.disclaimer}</p>
          <p className={styles.disclaimer}>{m.layout.dataNote}</p>
        </div>
        <div className={styles.side}>
          <nav aria-label={m.layout.footerNav}>
            <ul className={styles.links}>
              {NAV_ITEMS.map((item) => (
                <li key={item.to}>
                  <Link to={item.to}>{m.nav[item.label]}</Link>
                </li>
              ))}
            </ul>
          </nav>
          <ThemeSwitcher />
        </div>
      </div>
    </footer>
  );
}
