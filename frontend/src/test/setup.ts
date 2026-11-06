import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Mirror index.html, which declares <html lang="en">.
document.documentElement.lang = 'en';

afterEach(() => {
  cleanup();
});
