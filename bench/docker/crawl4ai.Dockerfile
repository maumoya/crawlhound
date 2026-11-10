FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy@sha256:a34c7c0c74cc5c94f39bedbee2cb220b1e0c0408c78610eade5ab1db32c3294f
RUN pip install --no-cache-dir crawl4ai==0.6.3 playwright==1.49.1
RUN useradd -m bench
USER bench
WORKDIR /work
