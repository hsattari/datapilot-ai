FROM node:22-slim AS build
WORKDIR /app
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ .
ARG API_URL=http://api:8000
ENV API_URL=$API_URL
RUN npm run build
FROM node:22-slim
WORKDIR /app
COPY --from=build /app ./
USER node
EXPOSE 3000
CMD ["npm", "start"]
