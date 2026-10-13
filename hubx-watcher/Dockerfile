FROM python:3.12-slim
WORKDIR /app
COPY watcher.py config.json ./
ENV WATCHER_STATE=/data/state.json PYTHONUNBUFFERED=1
CMD ["python", "watcher.py", "--loop"]
