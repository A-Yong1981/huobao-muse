# Build the pinned Huobao Drama submodule together with this repository's
# small API compatibility patch for Muse storyboard media uploads.

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY huobao-drama/frontend/package.json huobao-drama/frontend/package-lock.json ./
RUN node -e "const fs=require('fs');const l=JSON.parse(fs.readFileSync('package-lock.json'));for(const p of Object.values(l.packages||{}))delete p.resolved;fs.writeFileSync('package-lock.json',JSON.stringify(l,null,2))" \
  && npm ci --no-audit --no-fund --registry=https://registry.npmjs.org
COPY huobao-drama/frontend/ ./
RUN npm run generate

FROM node:22-bookworm AS backend-build
WORKDIR /build/backend
COPY huobao-drama/backend/package.json huobao-drama/backend/package-lock.json ./
RUN node -e "const fs=require('fs');const l=JSON.parse(fs.readFileSync('package-lock.json'));for(const p of Object.values(l.packages||{}))delete p.resolved;fs.writeFileSync('package-lock.json',JSON.stringify(l,null,2))" \
  && npm ci --no-audit --no-fund --registry=https://registry.npmjs.org
COPY huobao-drama/backend/ ./
COPY patches/storyboard-media-fields.patch /tmp/storyboard-media-fields.patch
RUN apt-get update \
  && apt-get install -y --no-install-recommends patch \
  && patch --batch --forward -p1 -d /build < /tmp/storyboard-media-fields.patch \
  && rm -rf /var/lib/apt/lists/* /tmp/storyboard-media-fields.patch
RUN npm prune --omit=dev \
  && npm i tsx@^4.21.0 --no-save --no-audit --no-fund --registry=https://registry.npmjs.org

FROM node:22-bookworm-slim
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
