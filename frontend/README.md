# PREDIKTIA frontend

Spanish-first (es-CL) web frontend for PREDIKTIA, a quantitative football research platform.

> **Status: research preview.** `RESEARCH_ONLY` · `NOT_CERTIFIED` · `WAIT_REAL_SOURCE` · `NO_PUBLIC_PREDICTIONS`.
> This application shows no predictions, probabilities, odds, expected value, stakes,
> betting recommendations or model accuracy figures. Every fixture resolves to **ABSTAIN**.
> These rules are encoded in `src/content/productStatus.ts` and enforced by
> `src/app/productTruth.test.tsx` and `e2e/truth.spec.ts`.

## Stack

| Concern | Choice |
| --- | --- |
| UI | React 19, TypeScript 6.0 (strict, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`) |
| Build / dev server | Vite 8 |
| Routing | React Router 8 (data router, code-split pages) |
| Runtime validation | zod 4 — every API response is validated before it reaches the UI |
| Language | Typed message catalog (`src/i18n`), `Intl` formatting in `es-CL` / `America/Santiago` |
| Styling | Plain CSS: design tokens (`src/styles/tokens.css`, light + dark) and CSS Modules |
| Unit / component tests | Vitest 5, Testing Library, jsdom, axe-core |
| Browser tests | Playwright against the production build, using the installed Microsoft Edge |
| Lint | ESLint 9 with typescript-eslint (strict, type-checked), react-hooks, jsx-a11y (strict) |

No dependencies were added in Phase 2. TypeScript is pinned to 6.0.x and ESLint to 9.x
because current typescript-eslint and eslint-plugin-jsx-a11y do not support TypeScript 6.1+/7 or ESLint 10.

## Requirements

- Node.js **>= 22.12** (developed with 24.22.2 LTS; see `.nvmrc`) and npm.
- For `npm run test:e2e`: Microsoft Edge (default) or another Playwright channel via `PW_CHANNEL`.
- Optional: a running PREDIKTIA backend (`backend/`, FastAPI) for live data.

## Commands

All commands run from `frontend/`.

```sh
npm ci                # install exact locked dependencies (npm ci --offline works from a warm cache)
npm run dev           # dev server on http://localhost:5173 (proxies /api -> backend)
npm run typecheck     # tsc for app, tooling and e2e configs
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
| `VITE_API_BASE_URL` | browser (build time) | `/api` | Base URL for API calls. Same-origin path or `http(s)` URL. **Empty = data source disabled**: data views show "Fuente de datos no configurada" and no request is made. Invalid values also disable it. |
| `VITE_API_TIMEOUT_MS` | browser (build time) | `10000` | Per-request timeout (1000–60000 ms), covering headers and body. |
| `PREDIKTIA_API_PROXY_TARGET` | dev/preview server only | `http://127.0.0.1:8000` | Where `/api/*` is proxied (prefix stripped). Never embedded in the bundle. |

The backend does not enable CORS, so the browser always talks to it through a same-origin
prefix. In development and `vite preview` the Vite proxy provides it; **a production
deployment must provide the same `/api/* -> backend` mapping in its reverse proxy**, and
must serve `index.html` for unknown paths (client-side routing).

## User journey and URLs

Inicio → Competiciones → Partidos → Detalle del partido → Contexto de investigación.

| Path | Page |
| --- | --- |
| `/` | Inicio: purpose, journey, principles, platform status and product codes |
| `/competiciones?buscar=` | Catalog grouped by country; accent-insensitive search over the complete list |
| `/competiciones/:id?temporada=` | Competition facts, season selector, upcoming registered fixtures, searchable team directory |
| `/partidos?competicion&temporada&equipo&desde&hasta&estado&pagina` | Fixture browser; every filter lives in the URL (shareable, survives reload and back/forward) |
| `/partidos/:id` | Fixture detail: stored scores, Chile and UTC kickoff, sync note, research context (ABSTAIN) |
| `/investigacion`, `/metodologia` | Research approach, limitations, evidence requirements, ABSTAIN-first gates |

`estado` is `pendientes` or `finalizados`; `desde`/`hasta` are `YYYY-MM-DD` **UTC** days, because
that is how the backend filters. Phase 1 English URLs (`/fixtures`, `/fixtures/:id`, `/research`,
`/methodology`, their query parameters and methodology anchors) redirect to the Spanish ones.

URL parameters that cannot be used (unknown values, invalid or out-of-range ids, impossible dates)
are **listed in a notice and ignored**, never silently applied or "corrected".

## Backend integration

Only endpoints whose implementation, response model and semantics were verified in
`backend/app/api/routes` and `backend/app/schemas` are used, all read-only `GET`:

| Endpoint | Used for | Schema |
| --- | --- | --- |
| `GET /health` | "Servicio de datos" status badge | `{status: "ok", service}` |
| `GET /competitions` | catalog, competition page, names in lists/detail (no single-competition endpoint exists) | `list[CompetitionOut]` ordered by country, name |
| `GET /competitions/{id}/teams?season=` | team directory and team filter | `list[TeamOut]` (404 if competition/season unknown) |
| `GET /fixtures?competition_id&season&team_id&date_from&date_to&finished&limit&offset` | fixture browser, upcoming fixtures | `list[FixtureOut]` ordered by kickoff, id (`limit` ≤ 500) |
| `GET /fixtures/{id}` | fixture detail (404 if unknown) | `FixtureOut` |

Deliberately **not** used: `POST /sync/*` (writes) and `GET /providers/*` (calls external
providers and spends quota). The schemas live in `src/api/schemas.ts`.

### Pagination without a total count

The backend returns no total. Each page requests **26** rows and shows 25: the extra row only
decides whether "Página siguiente" is enabled. Result sets that are an exact multiple of 25 therefore
end on a full page with "Next" disabled and "Fin de los resultados". A deep link past the end
(`?pagina=9`) explains that the total is unknown and offers "Volver a la página anterior" and
"Ir a la primera página". No page count or result total is ever shown.

### Identifiers and numeric precision

Competition, team, fixture and season ids are PostgreSQL `integer` columns (1..2,147,483,647).

- URL ids are kept as canonical decimal strings (`src/lib/ids.ts`) and converted only when in range.
  Invalid ids (`abc`, `007`, `0`) and out-of-range ids (`2147483648`, `99999999999999999999`) get an
  explanatory page; **no request is made** and nothing is rounded.
- Before `JSON.parse`, response text is scanned for integer literals beyond `Number.MAX_SAFE_INTEGER`
  (`src/api/jsonNumbers.ts`); such a response is rejected and the exact literal is shown, instead of
  silently displaying a rounded id. Schemas additionally require ids within the `integer` range.

### Failure handling

`src/api/httpClient.ts` turns every failure into a typed `ApiError`; `src/api/describeError.ts`
classifies it without any text, and `src/i18n/failureText.ts` writes the Spanish copy. Nothing is
ever rendered in place of missing data:

| Situation | UI |
| --- | --- |
| no base URL configured | "Fuente de datos no configurada" (no request, no retry) |
| browser offline | banner "Sin conexión a internet." + "Sin conexión a internet" state; failed views retry automatically when the connection returns |
| network failure / proxy 5xx with non-JSON body | "Servicio de datos no disponible" (with HTTP status when known) + "Reintentar" |
| slow response | loading state; after 4 s a polite "Está tardando más de lo habitual…" |
| timeout | "La solicitud tardó demasiado" (states the seconds) + retry |
| non-JSON 2xx, malformed JSON, schema mismatch | "Respuesta con formato inesperado" naming the first offending record (row, id, field); one bad record rejects the whole page |
| integer JSON cannot represent exactly | "Identificador numérico no representable" with the literal |
| 404 | not-found state |
| valid empty list | specific empty state |

The schemas are deliberately **stricter** than the backend models (non-empty names, non-negative
integer goals, ids within the `integer` range). This is intentional fail-closed behaviour.

### Scores, statuses and time

- A score is labelled **Resultado final** only for FT/AET/PEN and AWD/WO (the backend's
  `FINAL_STATUSES`). Otherwise it is **Marcador en la última sincronización**; in-play fixtures say
  "al sincronizar" and "No es un marcador en vivo", with the recorded minute.
- `FixtureOut` exposes neither a sync timestamp nor the venue's time zone. Pages therefore say that
  data is as of the last synchronization (time not reported by the API) and show kickoffs in
  continental Chile time (`America/Santiago`, with its UTC offset) and in UTC on the detail page.
- Fixture lists are grouped by Chilean calendar day.

## Internationalisation

`src/i18n/messages.es-CL.ts` is the catalog and the source of the `Messages` type. Domain logic
returns codes (fixture phase, score kind, failure category, capability id) and only components turn
them into text via `useMessages()` / `useI18n()`. To add a language, create a catalog typed as
`Messages`, build an `I18n` with `createI18n(locale, messages, timeZone)` and pass it to
`<I18nProvider value={…}>`; `<html lang>` follows the active locale. Provider data (team,
competition and country names) is shown as stored, never translated.

## Accessibility

- Semantic landmarks, one `<h1>` per page, breadcrumbs (`aria-current`), skip link, `lang="es-CL"`.
- Focus moves to the page heading after client-side navigation and to the results heading after
  changing page; document titles update ("Partidos · PREDIKTIA").
- Menu below 960px follows the disclosure pattern (Escape closes and restores focus, outside press
  closes); on phones the filter form is a disclosure too, with active filters always visible as
  removable chips.
- Loading and result counts are polite `status` regions; errors are `alert`s; the offline banner is
  a persistent polite live region. Fields are labelled and wire hints/errors via `aria-describedby`.
- Light, dark or system theme (footer "Apariencia", persisted, applied before first paint);
  `prefers-reduced-motion` and forced-colors are respected.
- axe-core runs in jsdom on every page/state, and in the real browser (including colour contrast,
  system light/dark and explicit theme) at three viewport sizes.

## Test data policy

Synthetic records live only in `src/test/devOnlyData.ts` (unit tests) and
`e2e/support/devOnlyApi.ts` (browser tests), every name is prefixed `DEV_ONLY`, and a test asserts
that no application module references them. The browser fake serves `/api/*` by intercepting
requests inside the browser; it follows the backend's filtering, ordering, offset and limit so
pagination behaves like the real API. There is no mock or fake data source in the application.

## Prueba con el backend real (live backend smoke test)

Verified against the real FastAPI app with an intentionally unreachable database (so no real or
production data source is involved). From the repository's `backend/` (no `.env` present):

```sh
PYTHONDONTWRITEBYTECODE=1 DATABASE_URL='postgresql+psycopg://nobody:none@127.0.0.1:1/nonexistent' \
  DB_CONNECT_TIMEOUT_SECONDS=2 python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
# then, from frontend/:
E2E_LIVE_BACKEND=1 PREDIKTIA_API_PROXY_TARGET=http://127.0.0.1:8765 npm run test:e2e -- live-backend
```

Expected: the status badge shows **Conectado** (real `/health`), and the fixture browser shows
**Servicio de datos no disponible** (DB-backed endpoints return HTTP 500).

## Project structure

```
src/
  api/            HTTP client, typed errors, precision guard, zod schemas, API facade, React context
  app/            App root, routes (+ legacy redirects), lazy pages, cross-cutting tests
  components/
    layout/       header + menu, footer + theme switcher, offline banner, app layout
    ui/           Button, Card, StatusBadge, Notice, Breadcrumbs, fields, states, ProductCodes, IdProblem
  config/         typed environment parsing
  content/        product flags, product codes, capability and evidence ids (texts live in i18n)
  features/
    competitions/ catalog and competition pages
    fixtures/     fixture browser, detail, research context, filters, status/score domain logic
    system/       data-service status indicator
  hooks/          useResource (abortable, retries when back online), online status, theme, focus, title
  i18n/           es-CL catalog, I18n context/provider, formatters, failure texts
  lib/            ids, formatting, class-name helpers
  pages/          home, research, methodology, 404, route error
  styles/         design tokens and global styles
  test/           test-only helpers and DEV_ONLY synthetic data
e2e/              Playwright specs and DEV_ONLY browser fake API (e2e/support)
```

## Known limitations

- No real data is shown without a backend and a populated database; with none, the views show
  honest unavailable/empty states. The UI has only been exercised end to end against the browser
  fake and against a real backend with an unreachable database, not against a populated database.
- The API reports no synchronization time and no venue time zone; times are shown in Chile and UTC.
- Date filters are UTC days (backend semantics); a late-evening match in Chile may fall on the next UTC day.
- There is no single-competition endpoint and no seasons list: the competition page looks the
  competition up in the full list and only knows the current season; other seasons can be typed.
- There is no text search on fixtures (the API has none); team and competition search work on the
  complete lists those endpoints return.
- Team and competition logos are not displayed (third-party URLs); a decorative monogram is used.
- Only es-CL is shipped; the catalog is structured for more locales.
- Fixture statuses and scores are as of the backend's last synchronization, never live.
