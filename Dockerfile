FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MINDSPINE_ENV=production \
    MINDSPINE_DATA_DIR=/data \
    MINDSPINE_FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 fonts-noto-cjk curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY main.py model.py pseudoct_generator.py ai_report_service.py ./
COPY mindspine ./mindspine
COPY web ./web
COPY weights ./weights

RUN useradd --create-home --uid 10001 spineinsight \
    && mkdir -p /data \
    && chown -R spineinsight:spineinsight /app /data

USER spineinsight
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl --fail http://127.0.0.1:${PORT}/health/live || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers"]

