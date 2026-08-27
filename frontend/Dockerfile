FROM node:22-alpine AS build

WORKDIR /web

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Empty strings so the UI talks to nginx on the same origin.
ARG VITE_API_HTTP=
ARG VITE_API_WS=
ENV VITE_API_HTTP=$VITE_API_HTTP \
    VITE_API_WS=$VITE_API_WS

RUN npm run build

FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html

EXPOSE 80
