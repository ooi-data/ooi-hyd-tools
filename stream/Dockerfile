# Slim image for the live stream; build from the repo root:
#   docker build -f stream/Dockerfile -t ooi-hyd-stream .
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY stream/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# only the modules, not the package's local data dir
COPY ooi_hyd_tools/*.py /app/ooi_hyd_tools/
WORKDIR /app
ENTRYPOINT ["python", "-m", "ooi_hyd_tools.stream"]
