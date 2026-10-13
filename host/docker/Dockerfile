# The host services that need no Claude Code login, in one container: the error
# reporter and its status feed, the uptime monitor, the boards and the expiry
# monitor. The routines that work tickets with Claude Code need a real host.
#
#   docker compose -f host/docker/compose.yml up -d --build
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 mtoa

COPY host/services/ /opt/mtoa/services/
COPY claude/tools/mtoa_config.py claude/tools/with-credential.py /opt/mtoa/tools/
COPY host/docker/run.py /opt/mtoa/run.py

USER mtoa
ENV HOME=/home/mtoa PYTHONUNBUFFERED=1
WORKDIR /home/mtoa
EXPOSE 8791
CMD ["python3", "/opt/mtoa/run.py"]
