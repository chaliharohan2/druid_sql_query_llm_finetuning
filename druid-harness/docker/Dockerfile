# Native-arch Druid 35.0.0 image.
#
# Docker Hub apache/druid:35.0.0 is linux/amd64 only, so it cannot run on this
# ARM64 DGX Spark. The image contents under /opt/druid are Java jars + scripts
# (architecture-independent). Copy them onto a multi-arch Temurin 17 JRE.
FROM --platform=linux/amd64 apache/druid:35.0.0 AS official

FROM eclipse-temurin:17-jre-jammy
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=official /opt/druid /opt/druid
COPY druid.sh /druid.sh
RUN chmod 755 /druid.sh \
    && mkdir -p /opt/druid/var /opt/shared

VOLUME /opt/druid/var
WORKDIR /opt/druid
ENTRYPOINT ["/druid.sh"]
