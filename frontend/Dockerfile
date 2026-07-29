# Stage 1: build
FROM node:20-slim AS build
WORKDIR /app
# Use `npm install` rather than `npm ci`: no package-lock.json is committed, and
# `npm ci` hard-fails without one.  (Commit a lockfile and switch back to
# `npm ci` for reproducible builds.)
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: serve
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
