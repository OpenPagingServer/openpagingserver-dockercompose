FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git ca-certificates mariadb-client \
    ffmpeg festival festvox-kallpc16k festvox-kdlpc16k \
    festlex-poslex festlex-cmu \
    procps iproute2 iputils-ping \
    && apt-get install -y --no-install-recommends \
    festvox-don festvox-rablpc16k festlex-oald 2>/dev/null || true \
    && rm -rf /var/lib/apt/lists/*

COPY docker-entrypoint.sh /opt/docker-entrypoint.sh
COPY docker-init-db.py /opt/docker-init-db.py
RUN chmod +x /opt/docker-entrypoint.sh

FROM base

ARG CACHE_BUST=1
RUN curl -fsSL -H "X-OPS-Command: download" "https://install.openpagingserver.org/?ref=main" | tar -xz -C /opt \
    && mv /opt/OpenPagingServer* /opt/OpenPagingServer

WORKDIR /opt/OpenPagingServer

RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || \
    pip install --no-cache-dir pymysql flask python-dotenv requests cryptography waitress && \
    pip install --no-cache-dir mysql-connector-python requests Pillow

RUN mkdir -p /var/lib/openpagingserver/endpointmodules && \
    for module in cisco polycom yealink discord-webhook; do \
        curl -fsSL "https://install.openpagingserver.org/modules/${module}.opsepm" \
            -o "/var/lib/openpagingserver/endpointmodules/${module}.opsepm" || true; \
    done

RUN mkdir -p /var/lib/openpagingserver/assets

RUN mkdir -p /etc/openpagingserver/trustedca && \
    curl -fsSL "https://install.openpagingserver.org/rootca.crt" \
        -o /etc/openpagingserver/trustedca/OpenPagingServerProject.crt || true

EXPOSE 80/tcp 443/tcp 5060/tcp 5060/udp 8088/tcp 50010/tcp 8710/udp

ENTRYPOINT ["/opt/docker-entrypoint.sh"]
CMD ["python", "index.py"]
