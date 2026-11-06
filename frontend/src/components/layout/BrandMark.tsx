import styles from './BrandMark.module.css';

/** Logotype: a monogram with a stylised distribution curve plus the wordmark. */
export function BrandMark() {
  return (
    <span className={styles.brand}>
      <svg className={styles.mark} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <rect width="32" height="32" rx="8" className={styles.tile} />
        <path d="M5 24 C10 24 11 9 16 9 S22 24 27 24" className={styles.curve} />
        <line x1="16" y1="9" x2="16" y2="24" className={styles.axis} />
      </svg>
      <span className={styles.word}>PREDIKTIA</span>
    </span>
  );
}
