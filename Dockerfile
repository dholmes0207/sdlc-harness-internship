FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.139.2 uvicorn==0.51.0 pydantic==2.13.4

COPY app/  /app/app/
COPY data/ /app/data/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
