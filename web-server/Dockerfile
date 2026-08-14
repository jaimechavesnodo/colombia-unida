# Build de las dos SPAs + nginx que las sirve.
# Contexto de build: raíz del repo (ver docs/easypanel-setup.md).

FROM node:22-alpine AS build-web
WORKDIR /build
COPY apps/web/package.json ./
RUN npm install
COPY apps/web/ ./
RUN npm run build

FROM node:22-alpine AS build-console
WORKDIR /build
COPY apps/console/package.json ./
RUN npm install
COPY apps/console/ ./
RUN npm run build

FROM nginx:alpine
COPY web-server/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build-web /build/dist /usr/share/nginx/html/web
COPY --from=build-console /build/dist /usr/share/nginx/html/consola
EXPOSE 80
