# Build the pinned Huobao Drama submodule together with this repository's
# small API compatibility patch for Muse storyboard media uploads.

FROM docker.m.daocloud.io/library/node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY huobao-drama/frontend/package.json huobao-drama/frontend/package-lock.json ./
RUN node -e "const fs=require('fs');const l=JSON.parse(fs.readFileSync('package-lock.json'));for(const p of Object.values(l.packages||{}))delete p.resolved;fs.writeFileSync('package-lock.json',JSON.stringify(l,null,2))" \
  && npm ci --no-audit --no-fund --registry=https://registry.npmjs.org
COPY huobao-drama/frontend/ ./
COPY patches/huobao-frontend-custom.patch /tmp/huobao-frontend-custom.patch
RUN apt-get update && apt-get install -y --no-install-recommends patch && rm -rf /var/lib/apt/lists/* \
  && (patch --batch --reverse --dry-run -p2 < /tmp/huobao-frontend-custom.patch >/dev/null 2>&1 || patch --batch --forward -p2 < /tmp/huobao-frontend-custom.patch)
RUN npm run generate

FROM docker.m.daocloud.io/library/node:22-bookworm AS backend-build
WORKDIR /build/backend
COPY huobao-drama/backend/package.json huobao-drama/backend/package-lock.json ./
RUN node -e "const fs=require('fs');const l=JSON.parse(fs.readFileSync('package-lock.json'));for(const p of Object.values(l.packages||{}))delete p.resolved;fs.writeFileSync('package-lock.json',JSON.stringify(l,null,2))" \
  && npm ci --omit=dev --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org
RUN NODE_ENV=production npm i tsx@^4.21.0 --no-save --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org \
  && npm rebuild better-sqlite3 --build-from-source
RUN npm install --prefix /tmp/tsx tsx@^4.21.0 --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org \
  && cp -a /tmp/tsx/node_modules/. ./node_modules/ \
  && ./node_modules/.bin/tsx --version \
  && rm -rf /tmp/tsx
COPY huobao-drama/backend/ ./
COPY patches/huobao-backend-custom.patch /tmp/huobao-backend-custom.patch
RUN (patch --batch --reverse --dry-run -p2 < /tmp/huobao-backend-custom.patch >/dev/null 2>&1 || patch --batch --forward -p2 < /tmp/huobao-backend-custom.patch)
COPY patches/storyboard-media-fields.patch /tmp/storyboard-media-fields.patch
RUN patch --batch --forward -p1 -d /build < /tmp/storyboard-media-fields.patch \
  && rm -f /tmp/storyboard-media-fields.patch

FROM docker.m.daocloud.io/library/node:22-bookworm-slim
ARG HUOBAO_VERSION=muse-build
ENV NODE_ENV=production \
    HUOBAO_VERSION=${HUOBAO_VERSION} \
    PORT=5679 \
    FFMPEG_BIN=/usr/bin/ffmpeg \
    FFPROBE_BIN=/usr/bin/ffprobe \
    HUOBAO_DATA_DIR=/app/data \
    SQLITE_PATH=/app/data/huobao.sqlite3 \
    WORKSPACE_PATH=/app/data/workspace \
    FRONTEND_DIST=/app/frontend-dist
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg \
  && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=backend-build /build/backend/src ./backend/src
COPY --from=backend-build /build/backend/node_modules ./backend/node_modules
COPY --from=backend-build /build/backend/package.json ./backend/package.json
COPY --from=backend-build /build/backend/tsconfig.json ./backend/tsconfig.json
COPY huobao-drama/backend/workspace ./workspace-template
COPY --from=frontend-build /build/frontend/.output/public ./frontend-dist
COPY huobao-drama/docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh && mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 5679
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD node -e "fetch('http://localhost:'+(process.env.PORT||5679)+'/api/v1/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
ENTRYPOINT ["./entrypoint.sh"]
