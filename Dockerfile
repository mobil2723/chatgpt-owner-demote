FROM browserless/chrome:latest

USER root
RUN apt-get update && apt-get install -y python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend
RUN mkdir -p /app/backend/static

ENV PYTHONUNBUFFERED=1
ENV HEADLESS=true
ENV CHROME_BIN=/usr/bin/google-chrome

# 直接调用 Python，不需要任何变量！
CMD ["python3", "main.py"]
