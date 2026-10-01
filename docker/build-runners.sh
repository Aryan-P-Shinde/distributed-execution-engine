#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
docker build -t dwee-runner-python:latest runner-python
docker build -t dwee-runner-node:latest runner-node
