FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy@sha256:a34c7c0c74cc5c94f39bedbee2cb220b1e0c0408c78610eade5ab1db32c3294f
COPY crawl4ai/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
RUN useradd -m bench
USER bench
WORKDIR /work
