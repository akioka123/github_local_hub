FROM python:3.12-slim
WORKDIR /app
COPY hub ./hub
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HUB_HOST=0.0.0.0 HUB_DATA=/data HUB_BACKUPS=/backups
RUN mkdir /data /backups
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"
CMD ["python", "-m", "hub"]
