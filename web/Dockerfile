# 职引者 ZHIYIN · Next.js 控制台镜像
# 构建前先在项目根执行： npm --prefix web run sync-legacy
# docker compose 会在 web 服务启动前自动执行该同步（见 docker-compose.yml）

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
# Next.js 会把 rewrites 的 destination 固化进构建产物，因此 BFF 地址必须在 build 阶段注入。
# 运行时再改 ZHIYIN_BFF_URL 只影响服务端组件（控制台状态页），不影响 rewrite。
ARG ZHIYIN_BFF_URL=http://api:8848
ENV NEXT_TELEMETRY_DISABLED=1 \
    ZHIYIN_BFF_URL=${ZHIYIN_BFF_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
# runner 侧同时设置，供服务端组件（控制台状态页 / 部署探针）在运行时读取。
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    ZHIYIN_BFF_URL=http://api:8848
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.mjs ./next.config.mjs
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD wget -qO- http://127.0.0.1:3000/api/health >/dev/null || exit 1
CMD ["npm", "run", "start"]
