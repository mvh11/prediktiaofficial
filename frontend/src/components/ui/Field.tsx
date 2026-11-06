import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react';
import styles from './Field.module.css';

interface FieldIds {
  readonly inputId: string;
  readonly describedBy: string | undefined;
}

interface FieldFrameProps {
  readonly label: ReactNode;
  readonly hint?: ReactNode;
  readonly error?: ReactNode;
  readonly children: (ids: FieldIds) => ReactNode;
}

/** Label + control + hint + error, wired with htmlFor / aria-describedby. */
function FieldFrame({ label, hint, error, children }: FieldFrameProps) {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined;
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={inputId}>
        {label}
      </label>
      {children({ inputId, describedBy })}
      {hint ? (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className={styles.error}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  readonly label: ReactNode;
  readonly hint?: ReactNode;
  readonly error?: ReactNode;
}

export function SelectField({ label, hint, error, children, ...rest }: SelectFieldProps) {
  return (
    <FieldFrame label={label} hint={hint} error={error}>
      {({ inputId, describedBy }) => (
        <select id={inputId} className={styles.control} aria-describedby={describedBy} aria-invalid={error ? true : undefined} {...rest}>
          {children}
        </select>
      )}
    </FieldFrame>
  );
}

export interface InputFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  readonly label: ReactNode;
  readonly hint?: ReactNode;
  readonly error?: ReactNode;
}

export function InputField({ label, hint, error, ...rest }: InputFieldProps) {
  return (
    <FieldFrame label={label} hint={hint} error={error}>
      {({ inputId, describedBy }) => (
        <input id={inputId} className={styles.control} aria-describedby={describedBy} aria-invalid={error ? true : undefined} {...rest} />
      )}
    </FieldFrame>
  );
}
