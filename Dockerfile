FROM python:3.14.7-alpine3.24

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY fetch.py /app/fetch.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/fetch.py", "loop"]
