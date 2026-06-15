// Copyright 2024-2026 Firefly Software Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.firefly.flydocs.sdk;

import com.firefly.flydocs.sdk.error.FlydocsClientException;
import com.firefly.flydocs.sdk.error.FlydocsHttpException;
import com.firefly.flydocs.sdk.error.FlydocsTimeoutException;
import com.firefly.flydocs.sdk.model.Extraction;
import com.firefly.flydocs.sdk.model.ExtractionListQuery;
import com.firefly.flydocs.sdk.model.ExtractionListResponse;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.ExtractionResultEnvelope;
import com.firefly.flydocs.sdk.model.ExtractionStatus;
import com.firefly.flydocs.sdk.model.PostProcessingStatus;
import com.firefly.flydocs.sdk.model.SubmitExtractionRequest;
import com.firefly.flydocs.sdk.model.VersionInfo;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.jspecify.annotations.Nullable;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.util.UriBuilder;
import reactor.core.publisher.Mono;
import reactor.netty.http.client.HttpClient;
import reactor.netty.resources.ConnectionProvider;
import reactor.util.retry.Retry;

/**
 * Reactive (non-blocking) client for the flydocs v1 HTTP API.
 *
 * <p>Built on Spring {@link WebClient} + Reactor Netty. Construct once
 * per application and inject; the client is thread-safe and re-uses
 * its connection pool across calls.</p>
 *
 * <pre>{@code
 * FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
 *         .baseUrl("http://localhost:8080")
 *         .timeout(Duration.ofSeconds(60))
 *         .build();
 *
 * flydocs.extract(request)
 *        .subscribe(result -> ...);
 *
 * // Async extractions:
 * flydocs.extractions().create(submitReq, "idem-key").subscribe(...);
 * }</pre>
 */
public class FlydocsClientAsync implements AutoCloseable {
    private static final String USER_AGENT = "flydocs-sdk-java/26.6.0";
    /** Default timeout when the caller does not override. */
    public static final Duration DEFAULT_TIMEOUT = Duration.ofSeconds(60);

    private final WebClient http;
    private final ObjectMapper mapper;
    private final Duration timeout;
    @Nullable
    private final ConnectionProvider ownedConnectionProvider;
    private final int maxAttempts;
    @Nullable
    private final Duration retryMinBackoff;
    private final Extractions extractions;

    private FlydocsClientAsync(Builder b) {
        this.timeout = b.timeout == null ? DEFAULT_TIMEOUT : b.timeout;
        this.mapper = b.objectMapper != null ? b.objectMapper : defaultMapper();
        this.maxAttempts = Math.max(1, b.maxAttempts);
        this.retryMinBackoff = b.retryMinBackoff;
        this.ownedConnectionProvider = ConnectionProvider.builder("flydocs-sdk")
                .maxConnections(b.maxConnections)
                .pendingAcquireTimeout(b.pendingAcquireTimeout)
                .build();
        HttpClient nettyClient = HttpClient.create(this.ownedConnectionProvider)
                .responseTimeout(this.timeout);
        WebClient.Builder wb = WebClient.builder()
                .baseUrl(b.baseUrl)
                .clientConnector(new ReactorClientHttpConnector(nettyClient))
                .defaultHeader(HttpHeaders.ACCEPT, MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader(HttpHeaders.USER_AGENT, USER_AGENT)
                .exchangeStrategies(ExchangeStrategies.builder()
                        .codecs(c -> c.defaultCodecs().maxInMemorySize(b.maxInMemorySize))
                        .build());
        if (b.apiKey != null && !b.apiKey.isEmpty()) {
            wb.defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + b.apiKey);
        }
        if (b.defaultHeaders != null) {
            b.defaultHeaders.forEach((k, v) -> {
                if (v != null && !v.isEmpty()) {
                    wb.defaultHeader(k, v);
                }
            });
        }
        this.http = wb.build();
        this.extractions = new Extractions();
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Release the underlying Netty connection pool. Idempotent. */
    @Override
    public void close() {
        if (this.ownedConnectionProvider != null) {
            this.ownedConnectionProvider.disposeLater().block(Duration.ofSeconds(5));
        }
    }

    // ------------------------------------------------------------------
    // Identity / health
    // ------------------------------------------------------------------

    /** {@code GET /api/v1/version} */
    public Mono<VersionInfo> version() {
        return requestJson("GET", uri -> uri.path("/api/v1/version").build(), null, null, null, VersionInfo.class);
    }

    /**
     * {@code GET /actuator/health/{probe}} — typically {@code readiness} or
     * {@code liveness}. The shape is owned by pyfly, not flydocs, so this
     * returns a raw map.
     */
    public Mono<Map<String, Object>> health(String probe) {
        TypeReference<Map<String, Object>> ref = new TypeReference<>() {};
        return requestJsonRef("GET", uri -> uri.path("/actuator/health/{p}").build(probe), null, null, null, ref);
    }

    public Mono<Map<String, Object>> health() {
        return health("readiness");
    }

    // ------------------------------------------------------------------
    // Sync extraction
    // ------------------------------------------------------------------

    /**
     * {@code POST /api/v1/extract:validate} — dry-run validator. Always
     * returns a body (errors arrive in the report, not as an HTTP error).
     */
    public Mono<Map<String, Object>> validate(ExtractionRequest request) {
        TypeReference<Map<String, Object>> ref = new TypeReference<>() {};
        return requestJsonRef(
                "POST",
                uri -> uri.path("/api/v1/extract:validate").build(),
                request,
                null,
                null,
                ref);
    }

    /** {@code POST /api/v1/extract} — run the full pipeline synchronously. */
    public Mono<ExtractionResult> extract(ExtractionRequest request) {
        return extract(request, null, null);
    }

    /** Same as {@link #extract(ExtractionRequest)} with an idempotency key + correlation id. */
    public Mono<ExtractionResult> extract(
            ExtractionRequest request,
            @Nullable String idempotencyKey,
            @Nullable String correlationId) {
        return requestJson(
                "POST",
                uri -> uri.path("/api/v1/extract").build(),
                request,
                idempotencyKey,
                correlationId,
                ExtractionResult.class);
    }

    // ------------------------------------------------------------------
    // Async extractions sub-resource
    // ------------------------------------------------------------------

    /** Returns the {@link Extractions} handle covering the async lifecycle endpoints. */
    public Extractions extractions() {
        return this.extractions;
    }

    /**
     * Sub-resource handle for {@code /api/v1/extractions/…} endpoints.
     *
     * <pre>{@code
     * Extraction queued = client.extractions().create(req, "idem-key").block();
     * Extraction current = client.extractions().get(id).block();
     * ExtractionResultEnvelope e = client.extractions().getResult(id, true, Duration.ofSeconds(60)).block();
     * Extraction cancelled = client.extractions().cancel(id).block();
     * ExtractionListResponse page = client.extractions().list(query).block();
     * }</pre>
     */
    public final class Extractions {

        private Extractions() {
            // package-private factory
        }

        /** {@code POST /api/v1/extractions} — enqueue. Returns 202 + initial {@code queued} {@link Extraction}. */
        public Mono<Extraction> create(SubmitExtractionRequest request) {
            return create(request, null, null);
        }

        /** {@link #create(SubmitExtractionRequest)} with idempotency. */
        public Mono<Extraction> create(SubmitExtractionRequest request, @Nullable String idempotencyKey) {
            return create(request, idempotencyKey, null);
        }

        public Mono<Extraction> create(
                SubmitExtractionRequest request,
                @Nullable String idempotencyKey,
                @Nullable String correlationId) {
            return requestJson(
                    "POST",
                    uri -> uri.path("/api/v1/extractions").build(),
                    request,
                    idempotencyKey,
                    correlationId,
                    Extraction.class);
        }

        /** {@code GET /api/v1/extractions/{id}} */
        public Mono<Extraction> get(String id) {
            return requestJson(
                    "GET",
                    uri -> uri.path("/api/v1/extractions/{id}").build(id),
                    null,
                    null,
                    null,
                    Extraction.class);
        }

        /**
         * Poll {@code GET /api/v1/extractions/{id}} until the extraction reaches
         * a terminal status (succeeded, failed, cancelled), then emit the final
         * {@link Extraction}. Errors with {@link java.util.concurrent.TimeoutException}
         * when the deadline elapses while the worker is still mid-flight.
         */
        public Mono<Extraction> waitForCompletion(
                String id, Duration pollInterval, Duration timeout) {
            return Mono.defer(() -> get(id))
                    .flatMap(s -> s.isTerminal() ? Mono.just(s) : Mono.<Extraction>empty())
                    .repeatWhenEmpty(Integer.MAX_VALUE, attempts -> attempts.delayElements(pollInterval))
                    .timeout(timeout);
        }

        /** Default poll (2s) and timeout (10m). */
        public Mono<Extraction> waitForCompletion(String id) {
            return waitForCompletion(id, Duration.ofSeconds(2), Duration.ofMinutes(10));
        }

        /** {@code DELETE /api/v1/extractions/{id}} — only valid while {@code status==queued}. */
        public Mono<Extraction> cancel(String id) {
            return requestJson(
                    "DELETE",
                    uri -> uri.path("/api/v1/extractions/{id}").build(id),
                    null,
                    null,
                    null,
                    Extraction.class);
        }

        /** {@code GET /api/v1/extractions/{id}/result}, optionally long-polling for grounded bboxes. */
        public Mono<ExtractionResultEnvelope> getResult(String id, boolean waitForBboxes, Duration timeout) {
            return requestJson(
                    "GET",
                    uri -> uri.path("/api/v1/extractions/{id}/result")
                            .queryParam("wait_for_bboxes", waitForBboxes)
                            .queryParam("timeout", timeout.toSeconds())
                            .build(id),
                    null,
                    null,
                    null,
                    ExtractionResultEnvelope.class);
        }

        public Mono<ExtractionResultEnvelope> getResult(String id) {
            return getResult(id, false, Duration.ofSeconds(60));
        }

        /** {@code GET /api/v1/extractions} — paginated, filterable. */
        public Mono<ExtractionListResponse> list(ExtractionListQuery query) {
            return requestJson(
                    "GET",
                    uri -> buildListUri(uri, query),
                    null,
                    null,
                    null,
                    ExtractionListResponse.class);
        }

        public Mono<ExtractionListResponse> list() {
            return list(ExtractionListQuery.defaults());
        }
    }

    private static java.net.URI buildListUri(UriBuilder builder, ExtractionListQuery q) {
        UriBuilder b = builder.path("/api/v1/extractions");
        if (q.statuses() != null && !q.statuses().isEmpty()) {
            List<String> wires = q.statuses().stream().map(ExtractionStatus::wire).toList();
            b.queryParam("status", String.join(",", wires));
        }
        if (q.postProcessingStatuses() != null && !q.postProcessingStatuses().isEmpty()) {
            List<String> wires = q.postProcessingStatuses().stream().map(PostProcessingStatus::wire).toList();
            b.queryParam("post_processing_status", String.join(",", wires));
        }
        if (q.idempotencyKey() != null) {
            b.queryParam("idempotency_key", q.idempotencyKey());
        }
        if (q.createdAfter() != null) {
            b.queryParam("created_after", q.createdAfter().toString());
        }
        if (q.createdBefore() != null) {
            b.queryParam("created_before", q.createdBefore().toString());
        }
        return b.queryParam("limit", q.limit())
                .queryParam("offset", q.offset())
                .build();
    }

    // ------------------------------------------------------------------
    // Internal: one place to express headers + error mapping
    // ------------------------------------------------------------------

    private <T> Mono<T> requestJson(
            String method,
            java.util.function.Function<UriBuilder, java.net.URI> uriBuilder,
            @Nullable Object body,
            @Nullable String idempotencyKey,
            @Nullable String correlationId,
            Class<T> responseType) {
        return exchange(method, uriBuilder, body, idempotencyKey, correlationId)
                .flatMap(bytes -> decodeBody(bytes, responseType));
    }

    private <T> Mono<T> requestJsonRef(
            String method,
            java.util.function.Function<UriBuilder, java.net.URI> uriBuilder,
            @Nullable Object body,
            @Nullable String idempotencyKey,
            @Nullable String correlationId,
            TypeReference<T> ref) {
        return exchange(method, uriBuilder, body, idempotencyKey, correlationId)
                .flatMap(bytes -> decodeBodyRef(bytes, ref));
    }

    private Mono<byte[]> exchange(
            String method,
            java.util.function.Function<UriBuilder, java.net.URI> uriBuilder,
            @Nullable Object body,
            @Nullable String idempotencyKey,
            @Nullable String correlationId) {
        WebClient.RequestBodySpec spec = http.method(org.springframework.http.HttpMethod.valueOf(method))
                .uri(uriBuilder::apply);
        if (idempotencyKey != null && !idempotencyKey.isEmpty()) {
            spec = spec.header("Idempotency-Key", idempotencyKey);
        }
        if (correlationId != null && !correlationId.isEmpty()) {
            spec = spec.header("X-Correlation-Id", correlationId);
        }
        WebClient.RequestHeadersSpec<?> headers = spec;
        if (body != null) {
            headers = spec.contentType(MediaType.APPLICATION_JSON).bodyValue(body);
        }
        Mono<byte[]> exchange = headers.exchangeToMono(response -> response.bodyToMono(byte[].class)
                .defaultIfEmpty(new byte[0])
                .flatMap(bytes -> {
                    int status = response.statusCode().value();
                    if (status >= 400) {
                        return Mono.error(toHttpException(status, bytes));
                    }
                    return Mono.just(bytes);
                }))
                .onErrorMap(this::mapTransport);
        if (this.maxAttempts > 1) {
            Retry policy = Retry.backoff(
                            this.maxAttempts - 1L,
                            this.retryMinBackoff == null
                                    ? Duration.ofMillis(200)
                                    : this.retryMinBackoff)
                    .filter(FlydocsClientAsync::isTransient)
                    .transientErrors(true);
            exchange = exchange.retryWhen(policy);
        }
        return exchange;
    }

    /** 5xx + own-timeout + transport are retryable; 4xx is intentional state. */
    private static boolean isTransient(Throwable t) {
        if (t instanceof FlydocsHttpException http) {
            int s = http.statusCode();
            return s >= 500 && s < 600;
        }
        return t instanceof FlydocsTimeoutException || t instanceof FlydocsClientException;
    }

    private <T> Mono<T> decodeBody(byte[] bytes, Class<T> type) {
        if (bytes.length == 0) {
            return Mono.error(new FlydocsClientException("empty response body for " + type.getSimpleName()));
        }
        try {
            return Mono.just(mapper.readValue(bytes, type));
        } catch (Exception e) {
            return Mono.error(new FlydocsClientException("failed to decode " + type.getSimpleName(), e));
        }
    }

    private <T> Mono<T> decodeBodyRef(byte[] bytes, TypeReference<T> ref) {
        if (bytes.length == 0) {
            return Mono.error(new FlydocsClientException("empty response body"));
        }
        try {
            return Mono.just(mapper.readValue(bytes, ref));
        } catch (Exception e) {
            return Mono.error(new FlydocsClientException("failed to decode response", e));
        }
    }

    private FlydocsHttpException toHttpException(int status, byte[] body) {
        String raw = new String(body, java.nio.charset.StandardCharsets.UTF_8);
        @Nullable String code = null;
        @Nullable String title = null;
        @Nullable String detail = null;
        @Nullable Map<String, Object> payload = null;
        try {
            payload = mapper.readValue(body, new TypeReference<Map<String, Object>>() {});
            // flydocs RFC 7807 puts ``code`` at the top level; v0 sometimes
            // nested it under ``detail``. Try both.
            Object nested = payload.get("detail");
            List<Map<String, Object>> sources = new java.util.ArrayList<>();
            sources.add(payload);
            if (nested instanceof Map<?, ?> m) {
                sources.add(castMap(m));
            }
            for (Map<String, Object> src : sources) {
                if (code == null && src.get("code") instanceof String s) code = s;
                if (title == null && src.get("title") instanceof String s) title = s;
                if (detail == null && src.get("detail") instanceof String s) detail = s;
            }
        } catch (Exception ignored) {
            // body wasn't JSON; surface as typed HTTP exception anyway.
        }
        return new FlydocsHttpException(status, code, title, detail, payload, raw);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Map<?, ?> in) {
        return (Map<String, Object>) in;
    }

    private Throwable mapTransport(Throwable t) {
        if (t instanceof FlydocsHttpException) {
            return t;
        }
        if (t instanceof FlydocsClientException) {
            return t;
        }
        if (t instanceof java.util.concurrent.TimeoutException
                || t instanceof io.netty.handler.timeout.ReadTimeoutException
                || t instanceof io.netty.handler.timeout.WriteTimeoutException
                || t instanceof java.net.http.HttpTimeoutException) {
            return new FlydocsTimeoutException("request timed out", t);
        }
        return new FlydocsClientException(t.getClass().getSimpleName() + ": " + t.getMessage(), t);
    }

    private static ObjectMapper defaultMapper() {
        return new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    // ------------------------------------------------------------------
    // Builder
    // ------------------------------------------------------------------

    public static final class Builder {
        private @Nullable String baseUrl;
        private @Nullable String apiKey;
        private @Nullable Duration timeout;
        private @Nullable Map<String, String> defaultHeaders;
        private @Nullable ObjectMapper objectMapper;
        private int maxAttempts = 1;
        private @Nullable Duration retryMinBackoff;
        private int maxConnections = 50;
        private Duration pendingAcquireTimeout = Duration.ofSeconds(45);
        private int maxInMemorySize = 64 * 1024 * 1024;

        public Builder baseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
            return this;
        }

        /** Set the API key. When non-empty, the SDK adds {@code Authorization: Bearer <key>} on every request. */
        public Builder apiKey(@Nullable String apiKey) {
            this.apiKey = apiKey;
            return this;
        }

        public Builder timeout(Duration timeout) {
            this.timeout = timeout;
            return this;
        }

        public Builder defaultHeader(String name, String value) {
            if (defaultHeaders == null) {
                defaultHeaders = new HashMap<>();
            }
            defaultHeaders.put(name, value);
            return this;
        }

        public Builder objectMapper(ObjectMapper mapper) {
            this.objectMapper = mapper;
            return this;
        }

        public Builder maxAttempts(int maxAttempts) {
            this.maxAttempts = maxAttempts;
            return this;
        }

        public Builder retryMinBackoff(Duration backoff) {
            this.retryMinBackoff = backoff;
            return this;
        }

        public Builder maxConnections(int maxConnections) {
            this.maxConnections = maxConnections;
            return this;
        }

        public Builder pendingAcquireTimeout(Duration timeout) {
            this.pendingAcquireTimeout = timeout;
            return this;
        }

        public Builder maxInMemorySize(int bytes) {
            this.maxInMemorySize = bytes;
            return this;
        }

        public FlydocsClientAsync build() {
            if (baseUrl == null || baseUrl.isEmpty()) {
                throw new IllegalArgumentException("baseUrl is required");
            }
            return new FlydocsClientAsync(this);
        }
    }
}
