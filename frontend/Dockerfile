FROM node:22-alpine

WORKDIR /srv

# Copy the lockfile too, and install from it -- otherwise the image resolves
# fresh versions at build time and can differ from a local `npm install`.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev"]
