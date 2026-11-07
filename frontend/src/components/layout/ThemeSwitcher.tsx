import { useId } from 'react';
import { useTheme, type ThemePreference } from '../../hooks/useTheme.ts';
import { useMessages } from '../../i18n/i18n.ts';
import styles from './ThemeSwitcher.module.css';

const OPTIONS: readonly ThemePreference[] = ['system', 'light', 'dark'];

/** Native radio group (arrow keys move between options), persisted per browser. */
export function ThemeSwitcher() {
  const m = useMessages();
  const [preference, setPreference] = useTheme();
  const name = useId();
  return (
    <fieldset className={styles.switcher}>
      <legend className={styles.legend}>{m.theme.legend}</legend>
      <div className={styles.options}>
        {OPTIONS.map((option) => (
          <label key={option} className={styles.option}>
            <input
              type="radio"
              name={name}
              value={option}
              checked={preference === option}
              onChange={() => {
                setPreference(option);
              }}
            />
            <span>{m.theme[option]}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
