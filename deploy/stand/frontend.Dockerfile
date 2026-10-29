# syntax=docker/dockerfile:1.7
# Фронт стенда: та же сборка, что deploy/frontend.Dockerfile, но архив расширения скриншотов —
# стендовый (адрес http://localhost:8090 и доступ к http://localhost), возвраты не собираются.
FROM node:22-alpine AS builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM node:22-alpine AS extension
RUN apk add --no-cache zip
WORKDIR /src
COPY extension/package.json extension/package-lock.json extension/
RUN cd extension && npm ci --no-audit --no-fund
COPY extension/ extension/
COPY deploy/stand/build-extension.sh deploy/stand/build-extension.sh
ARG STAND_BACKEND_URL=http://localhost:8090
RUN sh deploy/stand/build-extension.sh "$STAND_BACKEND_URL" /out

FROM nginx:1.27-alpine
COPY deploy/nginx/frontend.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=extension /out/marketplace-auto-screenshots.zip /usr/share/nginx/html/extension/marketplace-auto-screenshots.zip
EXPOSE 80
