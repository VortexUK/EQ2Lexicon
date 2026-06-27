// ESLint flat config (ESLint 9+).
// React + react-hooks + react-refresh + typescript-eslint, recommended rules.
// Tightened where the codebase has agreed conventions; lax where the project
// disagrees with defaults (e.g. unused-vars is enforced by tsc strict, not ESLint).

import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules', '*.tsbuildinfo'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // The classic react-hooks rules — the ones that catch real bugs.
      //
      // NOT `reactHooks.configs.recommended.rules`: in eslint-plugin-react-hooks
      // v7 that preset bundles the React Compiler rules (set-state-in-effect,
      // refs, purity, immutability, preserve-manual-memoization, …) as errors.
      // This project doesn't use the React Compiler, and those rules flag
      // standard, correct patterns (e.g. fetch-then-setState in an effect), so
      // we opt out of them and keep just the two timeless ones.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      // Irregular whitespace inside regex literals is intentional here — e.g.
      // normaliseBossName's SPACE_VARIANTS class matches the Unicode space
      // variants that show up in ACT logs / curator data.
      'no-irregular-whitespace': ['error', { skipRegExps: true }],
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // TypeScript handles unused-vars more accurately.
      '@typescript-eslint/no-unused-vars': 'off',
      'no-unused-vars': 'off',
      // Allow the `(err as Error)` pattern that lives throughout the codebase;
      // P1-5 will replace it with `toErrorMessage`, after which this rule
      // could be re-enabled.
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
)
