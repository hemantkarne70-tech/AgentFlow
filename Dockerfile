FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system agentflow && adduser --system --ingroup agentflow agentflow
COPY requirements.txt .
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt
COPY app ./app
RUN mkdir -p /data && chown -R agentflow:agentflow /app /data
USER agentflow
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
