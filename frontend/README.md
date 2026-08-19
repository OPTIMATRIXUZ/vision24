# Vision 24 — frontend

Next 16 (App Router, React 19, Tailwind 4). Six pages: sources, live, dashboard,
ask, daily report, settings. See the [root README](../README.md) for the whole
system; this file covers only what is specific to this directory.

```bash
npm ci          # not `npm install` — see the lockfile note below
npm run dev -- -p 3001
```

Node 22 is required (`.nvmrc`, `engines`). `.npmrc` sets `engine-strict`, so an
older Node is refused at install time rather than producing a bundle that fails
in obscure ways — under Node 18 every jsdom test dies during collection with
errors that name dependencies and never mention Node.

## Talking to the API

`next.config.ts` rewrites `/api/*` to `API_ORIGIN` (default
`http://127.0.0.1:8020`). That rewrite is not a convenience: the backend issues
`v24_access` / `v24_refresh` as httpOnly cookies, and a browser on
`localhost:3001` talking to `127.0.0.1:8020` makes them cross-site, so
`SameSite=Lax` drops them and every request after login 401s. Same-origin also
removes CORS preflight and lets `src/proxy.ts` read the session cookie.

**`API_ORIGIN` is read at build time.** `rewrites()` is evaluated during
`next build` and baked into the routes manifest, so setting it on an already
built container does nothing. It is a Docker build argument for that reason.

## Language

The UI is Russian. Every user-visible string lives in `src/lib/i18n.ts`; English
is kept complete beside it for a future switcher. Two tests enforce this: one
fails if either locale is missing a key, and `i18n.coverage.test.ts` scans the
JSX for Latin prose in text nodes and in `placeholder` / `title` / `aria-label`
/ `alt` / `label` attributes. A hardcoded string is invisible in review, which
is how the app spent a long time being two languages at once.

## Tests

```bash
npm test         # vitest
npm run lint     # eslint --max-warnings 0; `next lint` no longer exists
npm run typecheck
```

Node is the default test environment because most of what is worth testing is
pure logic. A test that needs a DOM opts in with a docblock on line 1:

```ts
// @vitest-environment jsdom
```

`vitest.setup.ts` registers `@testing-library/react`'s cleanup explicitly —
Vitest runs without `globals` here, so RTL does not register it itself, and
without it rendered trees pile up in `document.body` across tests in a file.

## Regenerating the lockfile

`package-lock.json` must be generated on Linux:

```bash
docker run --rm -v "$PWD/package.json:/w/package.json:ro" -w /w node:22-slim \
  sh -c 'cp package.json /tmp/ && cd /tmp && npm install --package-lock-only \
         --silent && cat package-lock.json' > package-lock.json
```

npm records the optional platform packages of whichever machine writes the file
and normalises the rest away. A macOS-written lockfile omits the top-level
`@emnapi` entries that `@napi-rs/wasm-runtime` needs, and `npm ci` on Linux then
refuses to install at all — which breaks both CI and the Docker build. A
Linux-written lockfile works everywhere, macOS included. Do not "fix it up"
afterwards with `npm install` on macOS; that strips the entries straight back
out.

## Reading the framework docs

This is not the Next.js most references describe — see `AGENTS.md`. The copy in
`node_modules/next/dist/docs/` is the authority. Two changes that have already
caused silent no-ops here: middleware is `src/proxy.ts`, not `middleware.ts`,
and an error boundary's retry callback is `unstable_retry`, not `reset`.
