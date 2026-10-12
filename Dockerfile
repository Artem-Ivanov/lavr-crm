FROM node:24-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html vite.config.js ./
COPY src ./src
COPY public ./public

ARG VITE_DATA_MODE=api
ENV VITE_DATA_MODE=${VITE_DATA_MODE} VITE_API_BASE_URL=/api
RUN case "$VITE_DATA_MODE" in api|demo) npm run build ;; *) echo 'VITE_DATA_MODE must be api or demo' >&2; exit 1 ;; esac

FROM nginxinc/nginx-unprivileged:stable-alpine AS runtime
ENV API_PROXY_TARGET=http://host.docker.internal:8000
COPY docker/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist/ /usr/share/nginx/html/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1
