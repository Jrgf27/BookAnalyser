# Stage 1: build
FROM node:20-slim AS build
WORKDIR /app
# `npm ci` gives reproducible installs from the committed lockfile (and fails
# fast if package.json and package-lock.json drift out of sync).
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: serve
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
