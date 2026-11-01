# syntax=docker/dockerfile:1.28
FROM maven:3.10.0-eclipse-temurin-21@sha256:d49bca545509b98965a808175862337dfdd24e1a8a4b11005cc8ce0578e52e8c AS build
WORKDIR /src
COPY pom.xml ./
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests package

FROM eclipse-temurin:21.0.13_9-jre-alpine@sha256:82cf3d9ccbe1ed46252008d51b2f2329e6b9e8a2f4394c4bebefc3ea7f4b1372
RUN addgroup -S -g 10001 gateway && adduser -S -D -H -u 10001 -G gateway gateway
WORKDIR /app
COPY --from=build --chown=10001:10001 /src/target/pdf-gateway-api-*.jar /app/api.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=70", "-XX:+ExitOnOutOfMemoryError", "-jar", "/app/api.jar"]
