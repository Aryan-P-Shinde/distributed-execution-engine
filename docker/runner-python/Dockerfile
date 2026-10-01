# Runner image for python jobs. Network is off at run time, so anything a job
# needs (pytest here) must be baked in.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
