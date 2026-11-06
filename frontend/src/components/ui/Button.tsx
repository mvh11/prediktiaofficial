import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router';
import styles from './Button.module.css';
import { cx } from '../../lib/cx.ts';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost';

function classes(variant: ButtonVariant, className?: string): string {
  return cx(styles.button, styles[variant], className);
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: ButtonVariant;
  readonly children: ReactNode;
}

export function Button({ variant = 'primary', className, type = 'button', children, ...rest }: ButtonProps) {
  return (
    <button type={type} className={classes(variant, className)} {...rest}>
      {children}
    </button>
  );
}

export interface ButtonLinkProps extends LinkProps {
  readonly variant?: ButtonVariant;
}

export function ButtonLink({ variant = 'primary', className, children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={classes(variant, typeof className === 'string' ? className : undefined)} {...rest}>
      {children}
    </Link>
  );
}
