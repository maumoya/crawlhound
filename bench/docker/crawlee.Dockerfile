FROM mcr.microsoft.com/playwright:v1.64.1-noble@sha256:2f05f8a734509a7b3f26a912639488ee08f58a7b292d14d62271bcff9038768f
WORKDIR /opt/bench
COPY crawlee/package.json crawlee/package-lock.json ./
RUN npm ci --no-audit --no-fund
RUN useradd -m bench && chown -R bench /opt/bench
USER bench
WORKDIR /work
