FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMARTWMS_HOST=0.0.0.0 \
    SMARTWMS_PORT=8000 \
    SMARTWMS_DB=/data/wms.sqlite \
    SMARTWMS_LOG_LEVEL=info

WORKDIR /app

RUN groupadd --system smartwms \
    && useradd --system --gid smartwms --home-dir /app smartwms \
    && mkdir -p /data \
    && chown -R smartwms:smartwms /app /data

COPY --chown=smartwms:smartwms app.py requirements.txt ./
COPY --chown=smartwms:smartwms wms_agv ./wms_agv
COPY --chown=smartwms:smartwms static ./static

USER smartwms

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status == 200 else 1)"

ENTRYPOINT ["python", "app.py"]
