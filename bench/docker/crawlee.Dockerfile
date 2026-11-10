FROM mcr.microsoft.com/playwright:v1.48.2-jammy@sha256:96dc479be9a603227fafc7126bd7d5e8152ae3e63749f377b2c49a4314b28ae1
WORKDIR /opt/bench
RUN npm init -y >/dev/null && npm pkg set overrides.linkedom=0.16.11 && npm install --no-audit --no-fund crawlee@3.13.3 playwright@1.48.2
RUN useradd -m bench && chown -R bench /opt/bench
USER bench
WORKDIR /work
