FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY driver_vedirect/ driver_vedirect/
COPY manifest.json driver_vedirect/manifest.json
COPY config.default.ini .

# Git release tag, passed by the release workflow; the Driver reports "dev" when unset.
ARG DRIVER_VERSION
ENV DRIVER_VERSION=${DRIVER_VERSION}

ENTRYPOINT ["python", "-m", "driver_vedirect.main"]
CMD ["--config", "/app/config.ini"]
