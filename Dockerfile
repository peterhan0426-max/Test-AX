FROM python:3.12-slim-bookworm@sha256:5d9e6adc9abd558bc0ce05f9ab16cef71328916fc9728f6b81e337af86a737e2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY collector /app/collector
COPY saenong /app/saenong
RUN chmod -R a+rX /app && mkdir -p /var/saenong/data /var/saenong/backups
EXPOSE 8000
CMD ["python", "-u", "-m", "saenong.production"]
