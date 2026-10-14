FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
ARG VITE_API_URL=http://localhost:8000/api
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_OIDC_AUTHORITY=
ARG VITE_OIDC_CLIENT_ID=
ARG VITE_OIDC_REDIRECT_URI=http://localhost:4173/
ARG VITE_OIDC_ROLES_CLAIM=roles
ENV VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY \
	VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID \
	VITE_OIDC_REDIRECT_URI=$VITE_OIDC_REDIRECT_URI \
	VITE_OIDC_ROLES_CLAIM=$VITE_OIDC_ROLES_CLAIM
RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
RUN npm install --global serve && chown -R node:node /app
COPY --from=build --chown=node:node /app/dist ./dist
USER node
EXPOSE 4173
CMD ["serve", "-s", "dist", "-l", "4173"]
