FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 9001

# Bind to 0.0.0.0 so the port is reachable from the host (main.py binds 127.0.0.1).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9001"]
