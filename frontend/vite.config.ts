import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv, type ProxyOptions } from 'vite';

/**
 * The PREDIKTIA FastAPI backend does not enable CORS, so the browser talks to it
 * through a same-origin `/api` prefix. In development and `vite preview` this
 * proxy forwards `/api/*` to the backend with the prefix stripped. In a real
 * deployment the same mapping must be provided by the hosting reverse proxy.
 *
 * PREDIKTIA_API_PROXY_TARGET is read server-side only (no VITE_ prefix), so it
 * is never embedded in the client bundle.
 */
const DEFAULT_PROXY_TARGET = 'http://127.0.0.1:8000';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.PREDIKTIA_API_PROXY_TARGET || DEFAULT_PROXY_TARGET;
  const apiProxy: Record<string, ProxyOptions> = {
    '/api': {
      target,
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
  };

  return {
    plugins: [react()],
    server: { port: 5173, strictPort: true, proxy: apiProxy },
    preview: { port: 4173, strictPort: true, proxy: apiProxy },
    build: { sourcemap: true, target: 'es2022' },
  };
});
