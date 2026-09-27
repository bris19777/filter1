FROM python:3.12-slim

WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# server app
COPY server/ ./
# agent source, served to clients by /agent.py
COPY agent/agent.py ./client_agent.py

ENV FILTER1_DATA=/data
EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "--timeout", "120", "app:app"]
