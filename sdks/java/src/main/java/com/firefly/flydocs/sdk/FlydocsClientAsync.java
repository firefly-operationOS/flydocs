/*
 * Copyright 2026 Firefly Software Solutions Inc
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.firefly.flydocs.sdk;

import com.firefly.flydocs.sdk.error.FlydocsClientException;
import com.firefly.flydocs.sdk.error.FlydocsHttpException;
import com.firefly.flydocs.sdk.error.FlydocsTimeoutException;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.JobListResponse;
import com.firefly.flydocs.sdk.model.JobResult;
import com.firefly.flydocs.sdk.model.JobStatusResponse;
import com.firefly.flydocs.sdk.model.SubmitJobRequest;
import com.firefly.flydocs.sdk.model.SubmitJobResponse;
import com.firefly.flydocs.sdk.model.VersionInfo;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.time.Duration;
import java.time.OffsetDateTime;
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

/**
 * Reactive (non-blocking) client for the flydocs HTTP API.
 *
 * <p>Built on Spring {@link WebClient} + Reactor Netty. Construct once
 * per application and inject; the client is thread-safe and re-uses
 * its connection pool across calls.</p>
 *
 * <pre>{@code
 * FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
 *         .baseUrl("http://localhost:8400")
 *         .timeout(Duration.ofSeconds(60))
 *         .build();
 *
 * flydocs.extract(request)
 *        .subscribe(result -> ...);
 * }</pre>
 */
public class FlydocsClientAsync {
    private static final String USER_AGENT = "flydocs-sdk-java/0.1.0";
    /** Default timeout when the caller does not override. */
    public static final Duration DEFAULT_TIMEOUT = Duration.ofSeconds(60);

    private final WebClient http;
    private final ObjectMapper mapper;
    private final Duration timeout;

    private FlydocsClientAsync(Builder b) {
        this.timeout = b.timeout == null ? DEFAULT_TIMEOUT : b.timeout;
        this.mapper = b.objectMapper != null ? b.objectMapper : defaultMapper();
        HttpClient nettyClient = HttpClient.create()
                .responseTimeout(this.timeout);
        WebClient.Builder wb = WebClient.builder()
                .baseUrl(b.baseUrl)
                .clientConnector(new ReactorClientHttpConnector(nettyClient))
                .defaultHeader(HttpHeaders.ACCEPT, MediaType.APPLICATION_JSON_VALUE)
                .defaultHeader(HttpHeaders.USER_AGENT, USER_AGENT)
                .codecs(c -> c.defaultCodecs().maxInMemorySize(64 * 1024 * 1024))
                .exchangeStrategies(ExchangeStrategies.builder().codecs(c ->
                        c.defaultCodecs().maxInMemorySize(64 * 1024 * 1024)).build());
        if (b.defaultHeaders != null) {
            b.defaultHeaders.forEach((k, v) -> {
                if (v != null && !v.isEmpty()) {
                    wb.defaultHeader(k, v);
                }
            });
        }
        this.http = wb.build();
    }

    public static Builder builder() {
        return new Builder();
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
    // Async-job lifecycle
    // ------------------------------------------------------------------

    /** {@code POST /api/v1/jobs} — enqueue. Returns 202 + initial {@code QUEUED} status. */
    public Mono<SubmitJobResponse> submitJob(SubmitJobRequest request) {
        return submitJob(request, null, null);
    }

    public Mono<SubmitJobResponse> submitJob(
            SubmitJobRequest request,
            @Nullable String idempotencyKey,
            @Nullable String correlationId) {
        return requestJson(
                "POST",
                uri -> uri.path("/api/v1/jobs").build(),
                request,
                idempotencyKey,
                correlationId,
                SubmitJobResponse.class);
    }

    /** {@code GET /api/v1/jobs/{id}} */
    public Mono<JobStatusResponse> getJob(String jobId) {
        return requestJson(
                "GET",
                uri -> uri.path("/api/v1/jobs/{id}").build(jobId),
                null,
                null,
                null,
                JobStatusResponse.class);
    }

    /**
     * Poll {@code GET /api/v1/jobs/{id}} until the job reaches a terminal
     * status (SUCCEEDED, PARTIAL_SUCCEEDED, FAILED, CANCELLED), then
     * emit the final {@link JobStatusResponse}.
     *
     * <p>Errors with {@link java.util.concurrent.TimeoutException} when
     * the deadline elapses before the job finishes. Inspect
     * {@link JobStatusResponse#status()} on success to decide what to
     * do next — the helper does not treat FAILED/CANCELLED as errors,
     * they're the caller's branching decision.</p>
     */
    public Mono<JobStatusResponse> waitForCompletion(
            String jobId, Duration pollInterval, Duration timeout) {
        return getJob(jobId)
                .flatMap(s -> s.isTerminal()
                        ? Mono.just(s)
                        : Mono.delay(pollInterval).then(waitForCompletion(jobId, pollInterval, timeout)))
                .timeout(timeout);
    }

    /** Same as {@link #waitForCompletion(String, Duration, Duration)} with default poll (2s) and timeout (10m). */
    public Mono<JobStatusResponse> waitForCompletion(String jobId) {
        return waitForCompletion(jobId, Duration.ofSeconds(2), Duration.ofMinutes(10));
    }

    /** {@code DELETE /api/v1/jobs/{id}} */
    public Mono<JobStatusResponse> cancelJob(String jobId) {
        return requestJson(
                "DELETE",
                uri -> uri.path("/api/v1/jobs/{id}").build(jobId),
                null,
                null,
                null,
                JobStatusResponse.class);
    }

    /** {@code GET /api/v1/jobs/{id}/result}, optionally long-polling for grounded bboxes. */
    public Mono<JobResult> getJobResult(String jobId, boolean waitForBboxes, Duration timeout) {
        return requestJson(
                "GET",
                uri -> uri.path("/api/v1/jobs/{id}/result")
                        .queryParam("wait_for_bboxes", waitForBboxes)
                        .queryParam("timeout", timeout.toSeconds())
                        .build(jobId),
                null,
                null,
                null,
                JobResult.class);
    }

    public Mono<JobResult> getJobResult(String jobId) {
        return getJobResult(jobId, false, Duration.ofSeconds(60));
    }

    /** {@code GET /api/v1/jobs} — paginated, filterable. Filters are joined with comma for CSV decoding. */
    public Mono<JobListResponse> listJobs(JobListFilter filter) {
        return requestJson(
                "GET",
                uri -> {
                    UriBuilder b = uri.path("/api/v1/jobs");
                    if (filter.status() != null && !filter.status().isEmpty()) {
                        b.queryParam("status", String.join(",", filter.status()));
                    }
                    if (filter.bboxRefineStatus() != null && !filter.bboxRefineStatus().isEmpty()) {
                        b.queryParam("bbox_refine_status", String.join(",", filter.bboxRefineStatus()));
                    }
                    if (filter.idempotencyKey() != null) {
                        b.queryParam("idempotency_key", filter.idempotencyKey());
                    }
                    if (filter.createdAfter() != null) {
                        b.queryParam("created_after", filter.createdAfter().toString());
                    }
                    if (filter.createdBefore() != null) {
                        b.queryParam("created_before", filter.createdBefore().toString());
                    }
                    return b.queryParam("limit", filter.limit())
                            .queryParam("offset", filter.offset())
                            .build();
                },
                null,
                null,
                null,
                JobListResponse.class);
    }

    public Mono<JobListResponse> listJobs() {
        return listJobs(JobListFilter.defaults());
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
        return headers.exchangeToMono(response -> response.bodyToMono(byte[].class)
                .defaultIfEmpty(new byte[0])
                .flatMap(bytes -> {
                    int status = response.statusCode().value();
                    if (status >= 400) {
                        return Mono.error(toHttpException(status, bytes));
                    }
                    return Mono.just(bytes);
                }))
                .onErrorMap(this::mapTransport);
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
            // flydocs emits ``code`` either at the top level OR nested under ``detail``.
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
            // Body wasn't JSON — fall back to raw text. Either way we
            // raise a typed HTTP exception, never a decode error.
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
        private @Nullable Duration timeout;
        private @Nullable Map<String, String> defaultHeaders;
        private @Nullable ObjectMapper objectMapper;

        public Builder baseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
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

        public FlydocsClientAsync build() {
            if (baseUrl == null || baseUrl.isEmpty()) {
                throw new IllegalArgumentException("baseUrl is required");
            }
            return new FlydocsClientAsync(this);
        }
    }

    /** Filter record for {@link #listJobs(JobListFilter)}. */
    public record JobListFilter(
            @Nullable List<String> status,
            @Nullable List<String> bboxRefineStatus,
            @Nullable String idempotencyKey,
            @Nullable OffsetDateTime createdAfter,
            @Nullable OffsetDateTime createdBefore,
            int limit,
            int offset) {

        public static JobListFilter defaults() {
            return new JobListFilter(null, null, null, null, null, 50, 0);
        }
    }
}
