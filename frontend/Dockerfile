# syntax=docker/dockerfile:1
#
# Next 16 standalone output.
#
# The one thing to know before editing: API_ORIGIN and NEXT_PUBLIC_GO2RTC_URL
# are BUILD arguments, not runtime environment variables, and passing them to
# `docker run` does nothing at all.
#
# next.config.ts reads API_ORIGIN at module scope and uses it in rewrites(),
# which Next evaluates during `next build` and bakes into the routes manifest.
# NEXT_PUBLIC_* values are substituted into the client bundle at build time by
# definition. So an image is built for one API origin; pointing it at a
# different backend means rebuilding. The failure mode if you forget is quiet —
# the default is http://127.0.0.1:8020, which inside a container resolves to the
# frontend container itself, so every API call fails with a connection error
# that looks like the backend is down.

ARG NODE_VERSION=22

# --- dependencies ----------------------------------------------------------
FROM node:${NODE_VERSION}-slim AS deps
WORKDIR /app

# .npmrc carries engine-strict, which is the point — a base image on the wrong
# major should fail here rather than produce a subtly broken bundle.
#
# package-lock.json must be generated on Linux. npm records the optional
# platform packages of whichever machine wrote it and normalises the rest away,
# and a macOS-written lockfile is missing the top-level @emnapi entries that
# @napi-rs/wasm-runtime needs — so `npm ci` here refuses to install at all.
# A Linux-written lockfile works on both, macOS included; the reverse does not.
# To regenerate:
#
#   docker run --rm -v "$PWD/package.json:/w/package.json:ro" -w /w node:22-slim \
#     sh -c 'cp package.json /tmp/ && cd /tmp && npm install --package-lock-only \
#            --silent && cat package-lock.json' > package-lock.json
#
# Do not "fix it up" afterwards with npm install on macOS — that normalises the
# entries straight back out.
COPY package.json package-lock.json .npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci

# --- build -----------------------------------------------------------------
FROM node:${NODE_VERSION}-slim AS builder
WORKDIR /app

# Where the browser reaches the API and the go2rtc relay. Both are baked in.
ARG API_ORIGIN=http://127.0.0.1:8020
ARG NEXT_PUBLIC_GO2RTC_URL=http://localhost:1984
ENV API_ORIGIN=${API_ORIGIN} \
    NEXT_PUBLIC_GO2RTC_URL=${NEXT_PUBLIC_GO2RTC_URL} \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# --- runtime ---------------------------------------------------------------
FROM node:${NODE_VERSION}-slim AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3001 \
    HOSTNAME=0.0.0.0

# `node` (uid 1000) ships with the base image.
USER node

# Standalone bundles the server and just the modules it traced, so no
# node_modules is copied. It does NOT copy `public` or `.next/static` — that is
# documented in next.config.ts and is why these are three separate COPYs rather
# than one. Missing them yields a server that runs and serves no CSS or images.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3001/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# server.js is what outputFileTracingRoot in next.config.ts guarantees lands at
# this path; without that setting Next nests it under the source tree's full
# path and this CMD silently points at nothing.
CMD ["node", "server.js"]
