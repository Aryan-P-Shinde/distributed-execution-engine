# The worker drives the *host's* Docker daemon through the mounted socket, so it
# only needs the docker CLI binary, not a daemon.
FROM docker:27-cli AS dockercli

FROM python:3.12-slim
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY pyproject.toml ./
COPY shared ./shared
COPY worker ./worker
RUN pip install --no-cache-dir .
CMD ["python", "-m", "worker.main"]
