import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

// W2: findBy*/waitFor resolve as soon as the awaited state is in the DOM; the timeout is only the budget before a test
// gives up. Testing Library's default of 1 s was routinely exceeded by real work (mocked fetch -> zod -> render) on a
// 4-CPU machine at 100 % load, failing tests whose DOM was still correctly "Cargando…". A missing state still fails.
configure({ asyncUtilTimeout: 5_000 });

// Mirror index.html, which declares <html lang="es-CL">.
document.documentElement.lang = 'es-CL';

// jsdom does not implement scrolling (used by <ScrollRestoration>) or canvas (probed by axe-core).
// No-op stubs keep the test output free of "Not implemented" noise; layout is checked in the browser suite.
window.scrollTo = () => undefined;
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

afterEach(() => {
  cleanup();
});
