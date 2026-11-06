import js from '@eslint/js';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'coverage', 'test-results', 'playwright-report'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.strict.rules,
      'react-refresh/only-export-components': ['error', { allowConstantExport: true }],
      // Scrollable regions must be keyboard-focusable (axe: scrollable-region-focusable).
      'jsx-a11y/no-noninteractive-tabindex': ['error', { tags: [], roles: ['tabpanel', 'region'], allowExpressionValues: true }],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', ignoreRestSiblings: true }],
    },
  },
  {
    files: ['src/**/*.test.{ts,tsx}', 'src/test/**'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    files: ['*.config.{js,ts}', 'e2e/**'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['eslint.config.js'],
    extends: [tseslint.configs.disableTypeChecked],
  },
);
