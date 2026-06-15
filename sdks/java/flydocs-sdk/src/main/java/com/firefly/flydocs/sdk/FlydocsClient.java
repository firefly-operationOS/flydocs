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

import com.firefly.flydocs.sdk.model.Extraction;
import com.firefly.flydocs.sdk.model.ExtractionListQuery;
import com.firefly.flydocs.sdk.model.ExtractionListResponse;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.ExtractionResultEnvelope;
import com.firefly.flydocs.sdk.model.SubmitExtractionRequest;
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
 *         .baseUrl("http://localhost:8080")
 *         .build();
 *
 * VersionInfo info        = flydocs.version();
 * ExtractionResult result = flydocs.extract(request);
 *
 * Extraction queued       = flydocs.extractions().create(submitReq, "idem-key");
 * Extraction current      = flydocs.extractions().get(id);
 * ExtractionResultEnvelope e = flydocs.extractions().getResult(id, true, Duration.ofSeconds(60));
 * Extraction cancelled    = flydocs.extractions().cancel(id);
 * ExtractionListResponse page = flydocs.extractions().list(query);
 * }</pre>
 */
public class FlydocsClient implements AutoCloseable {
    private final FlydocsClientAsync async;
    private final Extractions extractions;

    public FlydocsClient(FlydocsClientAsync async) {
        this.async = async;
        this.extractions = new Extractions();
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Release the underlying Netty pool. Forwards to the async client. */
    @Override
    public void close() {
        this.async.close();
    }

    /** Expose the async client for callers that need to drop down to {@link reactor.core.publisher.Mono}. */
    public FlydocsClientAsync async() {
        return this.async;
    }

    // ------------------------------------------------------------------
    // Identity / health
    // ------------------------------------------------------------------

    public VersionInfo version() {
        return async.version().block();
    }

    public Map<String, Object> health() {
        return async.health().block();
    }

    public Map<String, Object> health(String probe) {
        return async.health(probe).block();
    }

    // ------------------------------------------------------------------
    // Sync extraction
    // ------------------------------------------------------------------

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

    // ------------------------------------------------------------------
    // Async extractions sub-resource (blocking facade)
    // ------------------------------------------------------------------

    public Extractions extractions() {
        return this.extractions;
    }

    /** Blocking facade over {@link FlydocsClientAsync.Extractions}. */
    public final class Extractions {

        private Extractions() {}

        public Extraction create(SubmitExtractionRequest request) {
            return async.extractions().create(request).block();
        }

        public Extraction create(SubmitExtractionRequest request, @Nullable String idempotencyKey) {
            return async.extractions().create(request, idempotencyKey).block();
        }

        public Extraction create(
                SubmitExtractionRequest request,
                @Nullable String idempotencyKey,
                @Nullable String correlationId) {
            return async.extractions().create(request, idempotencyKey, correlationId).block();
        }

        public Extraction get(String id) {
            return async.extractions().get(id).block();
        }

        public Extraction waitForCompletion(String id, Duration pollInterval, Duration timeout) {
            return async.extractions().waitForCompletion(id, pollInterval, timeout).block();
        }

        public Extraction waitForCompletion(String id) {
            return async.extractions().waitForCompletion(id).block();
        }

        public Extraction cancel(String id) {
            return async.extractions().cancel(id).block();
        }

        public ExtractionResultEnvelope getResult(String id) {
            return async.extractions().getResult(id).block();
        }

        public ExtractionResultEnvelope getResult(String id, boolean waitForBboxes, Duration timeout) {
            return async.extractions().getResult(id, waitForBboxes, timeout).block();
        }

        public ExtractionListResponse list() {
            return async.extractions().list().block();
        }

        public ExtractionListResponse list(ExtractionListQuery query) {
            return async.extractions().list(query).block();
        }
    }

    /** Convenience builder that delegates to {@link FlydocsClientAsync.Builder}. */
    public static final class Builder {
        private final FlydocsClientAsync.Builder inner = FlydocsClientAsync.builder();

        public Builder baseUrl(String baseUrl) {
            inner.baseUrl(baseUrl);
            return this;
        }

        public Builder apiKey(@Nullable String apiKey) {
            inner.apiKey(apiKey);
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

        public Builder maxAttempts(int maxAttempts) {
            inner.maxAttempts(maxAttempts);
            return this;
        }

        public Builder retryMinBackoff(Duration backoff) {
            inner.retryMinBackoff(backoff);
            return this;
        }

        public Builder maxConnections(int maxConnections) {
            inner.maxConnections(maxConnections);
            return this;
        }

        public Builder pendingAcquireTimeout(Duration timeout) {
            inner.pendingAcquireTimeout(timeout);
            return this;
        }

        public Builder maxInMemorySize(int bytes) {
            inner.maxInMemorySize(bytes);
            return this;
        }

        public FlydocsClient build() {
            return new FlydocsClient(inner.build());
        }
    }
}
