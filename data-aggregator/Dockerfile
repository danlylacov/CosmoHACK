# syntax=docker/dockerfile:1

# ---- Build stage ----
FROM eclipse-temurin:25-jdk-jammy AS build
WORKDIR /app
ENV GRADLE_USER_HOME=/root/.gradle

COPY gradlew settings.gradle.kts build.gradle.kts gradle.properties ./
COPY gradle ./gradle
RUN chmod +x gradlew
RUN --mount=type=cache,target=/root/.gradle,sharing=locked \
    ./gradlew --no-daemon dependencies > /dev/null

COPY src ./src
RUN --mount=type=cache,target=/root/.gradle,sharing=locked \
    ./gradlew --no-daemon --build-cache bootJar -x test

# ---- Runtime stage ----
FROM eclipse-temurin:25-jre-jammy AS runtime
WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl

RUN groupadd --system app && useradd --system --gid app app
COPY --from=build /app/build/libs/*.jar app.jar
RUN chown app:app app.jar
USER app

EXPOSE 8088

ENTRYPOINT ["java", "-jar", "app.jar"]
