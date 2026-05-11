FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      openssh-client \
      ca-certificates \
      procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY dna_node ./dna_node
COPY tests ./tests

RUN mkdir -p /worker-cache /control-plane && \
    mkdir -p /root/.ssh && chmod 700 /root/.ssh

CMD ["python", "-m", "dna_node.main"]
