FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data directory for SQLite (mount a persistent volume here in production)
RUN mkdir -p /data
ENV DB_PATH=/data/rolefinder.db

EXPOSE 8080

CMD ["python", "run.py"]
