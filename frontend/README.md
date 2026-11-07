# PREDIKTIA frontend

Web frontend foundation for PREDIKTIA, a quantitative football research platform.

> **Status: research preview.** `RESEARCH_ONLY` · `NOT_CERTIFIED` · `NO_PUBLIC_PREDICTIONS`.
> This application shows no predictions, probabilities, odds, expected value, stakes,
> betting recommendations or model accuracy figures. Every fixture resolves to **ABSTAIN**.
> These rules are encoded in `src/content/productStatus.ts` and enforced by
> `src/app/productTruth.test.tsx`.

## Stack

| Concern | Choice |
| --- | --- |
| UI | React 19, TypeScript 6.0 (strict, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`) |
| Build / dev server | Vite 8 |
| Routing | React Router 8 (data router, code-split pages) |
| Runtime validation | zod 4 — every API response is validated before it reaches the UI |
| Styling | Plain CSS: design tokens (`src/styles/tokens.css`, light + dark) and CSS Modules |
| Unit / component tests | Vitest 5, Testing Library, jsdom, axe-core |
| Browser tests | Playwright against the production build, using the installed Microsoft Edge |
| Lint | ESLint 9 with typescript-eslint (strict, type-checked), react-hooks, jsx-a11y (strict) |

TypeScript is pinned to 6.0.x and ESLint to 9.x because current typescript-eslint and
eslint-plugin-jsx-a11y do not support TypeScript 6.1+/7 or ESLint 10.

## Requirements

- Node.js **>= 22.12** (developed with 24.22.2 LTS; see `.nvmrc`) and npm.
- For `npm run test:e2e`: Microsoft Edge (default) or another Playwright channel via `PW_CHANNEL`.
- Optional: a running PREDIKTIA backend (`backend/`, FastAPI) for live data.

## Commands

All commands run from `frontend/`.

```sh
npm ci                # install exact locked dependencies
npm run dev           # dev server on http://localhost:5173 (proxies /api -> backend)
npm run typecheck     # tsc for app and tooling configs
npm run lint          # eslint, zero warnings allowed
npm test              # vitest: unit, component, navigation, a11y, product-truth tests
npm run build         # typecheck + production build into dist/
npm run preview       # serve dist/ on http://localhost:4173 (proxies /api -> backend)
npm run test:e2e      # build + preview + Playwright in Edge (desktop, tablet, mobile)
npm run check         # typecheck + lint + test + build
```

## Configuration

Copy `.env.example` to `.env.local` and adjust. All values are optional.

| Variable | Where | Default | Meaning |
| --- | --- | --- | --- |
| `VITE_API_BASE_URL` | browser (build time) | `/api` | Base URL for API calls. Same-origin path or `http(s)` URL. **Empty = data source disabled**: data views show "Data source not configured" and no request is made. Invalid values also disable it. |
| `VITE_API_TIMEOUT_MS` | browser (build time) | `10000` | Per-request timeout (1000–60000 ms), covering headers and body. |
| `PREDIKTIA_API_PROXY_TARGET` | dev/preview server only | `http://127.0.0.1:8000` | Where `/api/*` is proxied (prefix stripped). Never embedded in the bundle. |

The backend does not enable CORS, so the browser always talks to it through a same-origin
prefix. In development and `vite preview` the Vite proxy provides it; **a production
deployment must provide the same `/api/* -> backend` mapping in its reverse proxy**, and
must serve `index.html` for unknown paths (client-side routing).

## Backend integration

Only endpoints whose implementation, response model and semantics were verified in
`backend/app/api/routes` and `backend/app/schemas` are used, all read-only:

| Endpoint | Used for | Schema |
| --- | --- | --- |
| `GET /health` | "Data service" status badge | `{status: "ok", service}` |
| `GET /competitions` | competition picker | `list[CompetitionOut]` |
| `GET /competitions/{id}/teams?season=` | team filter | `list[TeamOut]` (404 if competition/season unknown) |
| `GET /fixtures?competition_id&season&team_id&date_from&date_to&finished&limit&offset` | fixture list (ordered by kickoff, `limit` ≤ 500) | `list[FixtureOut]` |
| `GET /fixtures/{id}` | fixture detail (404 if unknown) | `FixtureOut` |

Deliberately **not** used: `POST /sync/*` (writes) and `GET /providers/*/status`
(calls external providers and spends quota). The schemas live in `src/api/schemas.ts`.

### Failure handling

`src/api/httpClient.ts` turns every failure into a typed `ApiError`, which
`src/api/describeError.ts` maps to honest UI states — nothing is ever rendered in place
of missing data:

| Situation | UI |
| --- | --- |
| no base URL configured | "Data source not configured" (no request made, no retry) |
| network failure / proxy 5xx with non-JSON body | "Data service unavailable" + retry |
| timeout | "Request timed out" + retry |
| non-JSON 2xx, malformed JSON, schema mismatch | "Unexpected response" — data is not displayed |
| 404 | not-found state |
| valid empty list | specific empty state |

The schemas are deliberately **stricter** than the backend models: names must be non-empty
and goal counts non-negative integers. A row that violates this rejects the whole response
as "Unexpected response" (fail-closed). This is intentional, not a contract mismatch.

A score is only labelled **Final score** for finished (FT/AET/PEN) or awarded (AWD/WO)
fixtures. Otherwise it is shown as **Score at last sync**, with a note that it is not a final
result.

### Live backend smoke test

Verified locally against the real FastAPI app with an intentionally unreachable database
(so no real or production data source is involved). From the repository's `backend/`
(no `.env` present), using its virtualenv:

```sh
PYTHONDONTWRITEBYTECODE=1 DATABASE_URL='postgresql+psycopg://nobody:none@127.0.0.1:1/nonexistent' \
  DB_CONNECT_TIMEOUT_SECONDS=2 python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
# then, from frontend/:
E2E_LIVE_BACKEND=1 PREDIKTIA_API_PROXY_TARGET=http://127.0.0.1:8765 npm run test:e2e -- live-backend
```

Expected: the status badge shows **Connected** (real `/health`), and the fixture browser
shows **Data service unavailable** (DB-backed endpoints return HTTP 500).

## Project structure

```
src/
  api/            HTTP client, typed errors, zod schemas, API facade, React context
  app/            App root, route table, lazy page modules, cross-cutting tests
  components/
    layout/       header + mobile nav, footer, app layout (skip link, Suspense)
    ui/           Button, Card, StatusBadge, Notice, fields, empty/error/loading states
  config/         typed environment parsing
  content/        product status and evidence requirements (single source of truth)
  features/
    fixtures/     fixture browser, detail page, status/score/filter domain logic
    system/       data-service status indicator
  hooks/          useResource (abortable async loading), focus and title helpers
  lib/            formatting and class-name helpers
  pages/          overview, research, methodology, 404, route error
  styles/         design tokens and global styles
  test/           test-only helpers and DEV_ONLY synthetic data
e2e/              Playwright specs
```

## Accessibility

- Semantic landmarks, one `<h1>` per page, skip link, `aria-current` navigation.
- Focus moves to the new page heading after client-side navigation; document title updates.
- Mobile menu follows the disclosure pattern (button with `aria-expanded`/`aria-controls`,
  Escape closes and restores focus, outside press closes).
- Loading = polite `status` region; errors = `alert`; form fields are labelled and wire
  hints/errors through `aria-describedby`/`aria-invalid`.
- Respects `prefers-color-scheme`, `prefers-reduced-motion` and forced-colors mode.
- axe-core runs in jsdom on every page/state, and in the real browser (including colour
  contrast, light and dark) on every page at three viewport sizes.

## Test data policy

Synthetic records used by tests live only in `src/test/devOnlyData.ts`, are labelled
`DEV_ONLY` in every name, and a test asserts no application module references them.
There is no mock or fake data source in the application.

## Known limitations

- No real data is shown without a backend and a populated database; with none, the
  fixture views show honest unavailable/empty states.
- Team and competition logos are not displayed (they are third-party URLs).
- UI copy is English only; there is no i18n layer yet.
- Fixture statuses and scores are as of the backend's last synchronization, not live.
- The fixture list has no total count from the API, so pagination is previous/next only. When a
  result set is an exact multiple of the page size, "Next" leads to an honest "No more fixtures" page.
