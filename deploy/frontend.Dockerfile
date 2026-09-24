# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS builder
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Расширение для кода получения возвратов: собирается здесь же и раздаётся статикой.
FROM node:22-alpine AS extension
WORKDIR /ext
# Адрес сервиса по умолчанию в настройках расширения: менеджер вводит только код.
ARG PUBLIC_DOMAIN=""
RUN apk add --no-cache zip
COPY extension/package.json extension/package-lock.json ./
RUN npm ci
COPY extension/ ./
RUN VITE_BACKEND_URL="${PUBLIC_DOMAIN:+https://$PUBLIC_DOMAIN}" npm run pack

FROM nginx:1.27-alpine AS runtime
COPY deploy/nginx/frontend.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=extension /ext/marketplace-auto-returns.zip /usr/share/nginx/html/extension/marketplace-auto-returns.zip

EXPOSE 80
