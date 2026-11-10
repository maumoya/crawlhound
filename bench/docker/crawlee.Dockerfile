FROM mcr.microsoft.com/playwright:v1.48.2-jammy@sha256:96dc479be9a603227fafc7126bd7d5e8152ae3e63749f377b2c49a4314b28ae1
WORKDIR /opt/bench
COPY crawlee/package.json crawlee/package-lock.json ./
RUN npm ci --no-audit --no-fund
RUN useradd -m bench && chown -R bench /opt/bench
USER bench
WORKDIR /work
