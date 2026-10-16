FROM python:3.12-slim
WORKDIR /srv
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m app
COPY apps/api/ .
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
