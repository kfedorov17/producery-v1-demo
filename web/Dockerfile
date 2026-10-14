# Producer UI — Next.js standalone build, non-root, port 3000 (docs/design.md section 14). Built from the repo root
# (`context: .`, `dockerfile: web/Dockerfile`). The producer roster is NOT baked in: compose mounts
# config/producers.yaml read-only at PRODUCERS_FILE (no account material inside an image).
ARG BASE_REGISTRY=public.ecr.aws/docker/library
FROM ${BASE_REGISTRY}/node:22-alpine AS deps
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM ${BASE_REGISTRY}/node:22-alpine AS build
WORKDIR /app/web
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_STANDALONE=1
COPY --from=deps /app/web/node_modules ./node_modules
COPY web/ ./
RUN npm run build

FROM ${BASE_REGISTRY}/node:22-alpine AS web
WORKDIR /app/web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0 \
    PRODUCERS_FILE=/app/config/producers.yaml
RUN addgroup -S producery && adduser -S -G producery producery
COPY --from=build /app/web/.next/standalone ./
COPY --from=build /app/web/.next/static ./.next/static
COPY --from=build /app/web/public ./public
USER producery
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --retries=12 CMD wget -qO- http://127.0.0.1:3000/api/health >/dev/null || exit 1
CMD ["node", "server.js"]
