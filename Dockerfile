# 1️⃣ Base image: Use official Node.js LTS version
FROM node:18-alpine

# 2️⃣ Set working directory
WORKDIR /usr/src/app

# 3️⃣ Copy package files and install dependencies
COPY package*.json ./
RUN npm install --production

# 4️⃣ Copy the rest of your app files (including .env if you're baking it — not recommended for secrets)
COPY . .

# 5️⃣ Set environment variable if needed (override in production)
ENV NODE_ENV=production

# 6️⃣ Expose the port your app listens on
EXPOSE 3000

# 7️⃣ Start the Node.js app
CMD ["node", "server.js"]
