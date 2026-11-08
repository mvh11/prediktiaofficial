import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config.ts';

export default defineConfig((env) =>
  mergeConfig(viteConfig(env), {
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      restoreMocks: true,
      unstubGlobals: true,
      unstubEnvs: true,
      // W2: the per-test limit must exceed the sum of a test's waits under load (several 5 s findBy* budgets, axe-core
      // scans that take seconds on a loaded machine); Vitest's 5 s default cut such tests off mid-wait.
      testTimeout: 30_000,
    },
  }),
);
