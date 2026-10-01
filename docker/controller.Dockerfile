FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY shared ./shared
COPY controller ./controller
RUN pip install --no-cache-dir .
CMD ["python", "-m", "controller.main"]
