import { useEffect, useId, useRef, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router';
import { BrandMark } from './BrandMark.tsx';
import { NAV_ITEMS } from './navigation.ts';
import styles from './SiteHeader.module.css';
import { cx } from '../../lib/cx.ts';

function navClass({ isActive }: { isActive: boolean }): string {
  return cx(styles.link, isActive && styles.active);
}

/**
 * Disclosure-pattern mobile menu: a real <button> with aria-expanded /
 * aria-controls; Escape closes it and returns focus to the toggle. It is
 * remounted (and therefore closed) on every route change by the header.
 */
function MobileNav() {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // While open: Escape closes and returns focus to the toggle; a pointer press
  // outside the menu closes it without moving focus.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && containerRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('pointerdown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('pointerdown', onPointerDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className={styles.mobile}>
      <button
        ref={toggleRef}
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <span className={styles.toggleIcon} data-open={open} aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span>{open ? 'Close menu' : 'Menu'}</span>
      </button>
      <nav id={panelId} aria-label="Mobile" className={styles.panel} hidden={!open}>
        <ul className={styles.panelList}>
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink to={item.to} end={item.end ?? false} className={navClass}>
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

export function SiteHeader() {
  const { pathname } = useLocation();
  return (
    <header className={styles.header}>
      <div className={cx('container', styles.inner)}>
        <Link to="/" className={styles.brandLink} aria-label="PREDIKTIA home">
          <BrandMark />
        </Link>
        <span className={styles.stage}>Research preview</span>
        <nav aria-label="Primary" className={styles.desktop}>
          <ul className={styles.list}>
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} end={item.end ?? false} className={navClass}>
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <MobileNav key={pathname} />
      </div>
    </header>
  );
}
