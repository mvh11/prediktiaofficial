import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Mirror index.html, which declares <html lang="es-CL">.
document.documentElement.lang = 'es-CL';

// jsdom does not implement scrolling (used by <ScrollRestoration>) or canvas (probed by axe-core).
// No-op stubs keep the test output free of "Not implemented" noise; layout is checked in the browser suite.
window.scrollTo = () => undefined;
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

afterEach(() => {
  cleanup();
});
