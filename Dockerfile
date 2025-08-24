# Dockerfile
FROM python:3.11-slim

# 讓 Python 不產生 .pyc，輸出不緩衝（log 即時）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app

# 先裝依賴，利用快取
COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 再拷貝程式碼
COPY . .

# 對外埠
EXPOSE 10000

# 啟動 uvicorn
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10000"]
