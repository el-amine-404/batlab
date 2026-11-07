# syntax=docker/dockerfile:1.28
FROM maven:3.10.0-eclipse-temurin-21@sha256:9673eef9ed503146591667ffb92eaaa22f1b8899ed60d9df0df4da3f3a1214a6 AS build
WORKDIR /src
COPY pom.xml ./
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests dependency:go-offline
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -DskipTests package

FROM eclipse-temurin:25.0.5_8-jre-alpine@sha256:e1898fd6a11fc965a994d8ace186fd3585aeb636cf8f05f47a723fcfee0b0918
RUN addgroup -S -g 10001 gateway && adduser -S -D -H -u 10001 -G gateway gateway
WORKDIR /app
COPY --from=build --chown=10001:10001 /src/target/pdf-gateway-api-*.jar /app/api.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=70", "-XX:+ExitOnOutOfMemoryError", "-jar", "/app/api.jar"]
