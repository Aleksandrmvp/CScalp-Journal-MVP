# CScalp trade journal — parser + FastAPI dashboard in one container.
FROM python:3.12-slim

# The log folders are named by Moscow-local date and the ingest loop resolves
# "today" from the local clock, so the container must run in Europe/Moscow.
ENV TZ=Europe/Moscow \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -fs /usr/share/zoneinfo/$TZ /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cscalp_journal ./cscalp_journal

# Container defaults; the log root, db path and bind host come from the image
# so `docker run` works standalone, and compose can still override them.
ENV CSCALP_WEB_HOST=0.0.0.0 \
    CSCALP_WEB_PORT=8777 \
    CSCALP_LOG_ROOT=/logs \
    CSCALP_DB=/data/journal.sqlite

EXPOSE 8777
CMD ["python", "-m", "cscalp_journal.web"]
