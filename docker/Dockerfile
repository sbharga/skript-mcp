FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce AS downloader

RUN apk add --no-cache curl

ARG PAPER_URL
ARG PAPER_SHA256
ARG SKRIPT_URL
ARG SKRIPT_SHA256
ARG SKBEE_URL
ARG SKBEE_SHA512
ARG SKRIPT_REFLECT_URL
ARG SKRIPT_REFLECT_SHA256
ARG SKRIPT_GUI_URL
ARG SKRIPT_GUI_SHA256

RUN set -eux; \
    mkdir -p /artifacts/plugins; \
    curl --fail --location --retry 3 "$PAPER_URL" --output /artifacts/paper.jar; \
    echo "$PAPER_SHA256  /artifacts/paper.jar" | sha256sum -c -; \
    curl --fail --location --retry 3 "$SKRIPT_URL" --output /artifacts/plugins/Skript.jar; \
    echo "$SKRIPT_SHA256  /artifacts/plugins/Skript.jar" | sha256sum -c -; \
    curl --fail --location --retry 3 "$SKBEE_URL" --output /artifacts/plugins/SkBee.jar; \
    echo "$SKBEE_SHA512  /artifacts/plugins/SkBee.jar" | sha512sum -c -; \
    curl --fail --location --retry 3 "$SKRIPT_REFLECT_URL" --output /artifacts/plugins/skript-reflect.jar; \
    echo "$SKRIPT_REFLECT_SHA256  /artifacts/plugins/skript-reflect.jar" | sha256sum -c -; \
    curl --fail --location --retry 3 "$SKRIPT_GUI_URL" --output /artifacts/plugins/skript-gui.jar; \
    echo "$SKRIPT_GUI_SHA256  /artifacts/plugins/skript-gui.jar" | sha256sum -c -

FROM eclipse-temurin:21-jre-noble@sha256:ae85b153d285d8001a75466b483f8858810f2a5a1052ee8d0f88651f4a16dd25

RUN apt-get update \
    && apt-get install --yes --no-install-recommends netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY --from=downloader /artifacts /opt/skript-server
COPY docker/entrypoint.sh /usr/local/bin/skript-server-entrypoint

RUN chmod 0755 /usr/local/bin/skript-server-entrypoint \
    && mkdir -p /server \
    && chown ubuntu:ubuntu /server

WORKDIR /server
EXPOSE 25575/tcp

ENTRYPOINT ["skript-server-entrypoint"]
