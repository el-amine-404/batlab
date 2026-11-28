# syntax=docker/dockerfile:1.29
FROM maven:3.10.2-eclipse-temurin-21@sha256:451c216d03f7cb0a0a26f61bb037fe24dcebaf6aa4e3168789911619ff9aaea0 AS build
WORKDIR /src
COPY pom.xml ./
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests package

FROM eclipse-temurin:25.0.5.1_2-jre-alpine@sha256:69c2a26e9894016e274c880fa542956d005b3755f84db765145185e87405bd89
RUN addgroup -S -g 10001 gateway && adduser -S -D -H -u 10001 -G gateway gateway
WORKDIR /app
COPY --from=build --chown=10001:10001 /src/target/pdf-gateway-api-*.jar /app/api.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=70", "-XX:+ExitOnOutOfMemoryError", "-jar", "/app/api.jar"]
