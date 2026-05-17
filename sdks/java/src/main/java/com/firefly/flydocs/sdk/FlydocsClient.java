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

import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.JobListResponse;
import com.firefly.flydocs.sdk.model.JobResult;
import com.firefly.flydocs.sdk.model.JobStatusResponse;
import com.firefly.flydocs.sdk.model.SubmitJobRequest;
import com.firefly.flydocs.sdk.model.SubmitJobResponse;
import com.firefly.flydocs.sdk.model.VersionInfo;
import java.time.Duration;
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * Blocking facade over {@link FlydocsClientAsync}.
 *
 * <p>Use from servlet apps, CLIs, or wherever else an event loop is
 * inconvenient. Internally calls {@code .block()} on the underlying
 * reactive client — if you're already on Reactor, prefer
 * {@link FlydocsClientAsync} directly.</p>
 *
 * <pre>{@code
 * FlydocsClient flydocs = FlydocsClient.builder()
 *         .baseUrl("http://localhost:8400")
 *         .build();
 *
 * VersionInfo info = flydocs.version();
 * ExtractionResult result = flydocs.extract(request);
 * }</pre>
 */
public class FlydocsClient {
    private final FlydocsClientAsync async;

    public FlydocsClient(FlydocsClientAsync async) {
        this.async = async;
    }

    public static Builder builder() {
        return new Builder();
    }

    public VersionInfo version() {
        return async.version().block();
    }

    public Map<String, Object> health() {
        return async.health().block();
    }

    public Map<String, Object> health(String probe) {
        return async.health(probe).block();
    }

    public Map<String, Object> validate(ExtractionRequest request) {
        return async.validate(request).block();
    }

    public ExtractionResult extract(ExtractionRequest request) {
        return async.extract(request).block();
    }

    public ExtractionResult extract(
            ExtractionRequest request,
            @Nullable String idempotencyKey,
            @Nullable String correlationId) {
        return async.extract(request, idempotencyKey, correlationId).block();
    }

    public SubmitJobResponse submitJob(SubmitJobRequest request) {
        return async.submitJob(request).block();
    }

    public SubmitJobResponse submitJob(
            SubmitJobRequest request,
            @Nullable String idempotencyKey,
            @Nullable String correlationId) {
        return async.submitJob(request, idempotencyKey, correlationId).block();
    }

    public JobStatusResponse getJob(String jobId) {
        return async.getJob(jobId).block();
    }

    public JobStatusResponse cancelJob(String jobId) {
        return async.cancelJob(jobId).block();
    }

    public JobResult getJobResult(String jobId) {
        return async.getJobResult(jobId).block();
    }

    public JobResult getJobResult(String jobId, boolean waitForBboxes, Duration timeout) {
        return async.getJobResult(jobId, waitForBboxes, timeout).block();
    }

    public JobListResponse listJobs() {
        return async.listJobs().block();
    }

    public JobListResponse listJobs(FlydocsClientAsync.JobListFilter filter) {
        return async.listJobs(filter).block();
    }

    /** Convenience builder that delegates to {@link FlydocsClientAsync.Builder}. */
    public static final class Builder {
        private final FlydocsClientAsync.Builder inner = FlydocsClientAsync.builder();

        public Builder baseUrl(String baseUrl) {
            inner.baseUrl(baseUrl);
            return this;
        }

        public Builder timeout(Duration timeout) {
            inner.timeout(timeout);
            return this;
        }

        public Builder defaultHeader(String name, String value) {
            inner.defaultHeader(name, value);
            return this;
        }

        public FlydocsClient build() {
            return new FlydocsClient(inner.build());
        }
    }
}
