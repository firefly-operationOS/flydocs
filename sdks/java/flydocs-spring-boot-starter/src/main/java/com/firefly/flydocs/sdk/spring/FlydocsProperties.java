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

package com.firefly.flydocs.sdk.spring;

import java.time.Duration;
import org.jspecify.annotations.Nullable;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Configuration properties for the flydocs SDK starter.
 *
 * <p>{@link #baseUrl} is required; everything else has a sane default.</p>
 *
 * <pre>{@code
 * flydocs:
 *   base-url: https://flydocs.example.com
 *   api-key: ${FLYDOCS_API_KEY}
 *   timeout: 60s
 *   webhook:
 *     secret: ${FLYDOCS_WEBHOOK_SECRET}
 * }</pre>
 */
@ConfigurationProperties(prefix = "flydocs")
public class FlydocsProperties {

    /**
     * Base URL of the flydocs service, e.g. {@code http://localhost:8080}.
     * Required.
     */
    @Nullable
    private String baseUrl;

    /**
     * API key sent as {@code Authorization: Bearer <key>} on every request.
     * Leave unset for unauthenticated local development.
     */
    @Nullable
    private String apiKey;

    /**
     * Per-call HTTP response timeout (start of request to last byte
     * received). Default 60s.
     */
    private Duration timeout = Duration.ofSeconds(60);

    /**
     * Maximum HTTP attempts per request (including the first). {@code 1}
     * disables retries; use {@code 2}-{@code 3} for transient-5xx
     * resilience. The SDK never retries 4xx.
     */
    private int maxAttempts = 1;

    /**
     * Initial backoff between retries, exponentially extended on each
     * subsequent retry with jitter.
     */
    private Duration retryMinBackoff = Duration.ofMillis(200);

    /** Max simultaneous HTTP connections in the Netty pool. */
    private int maxConnections = 50;

    /** Time a request will wait for a free connection from the pool. */
    private Duration pendingAcquireTimeout = Duration.ofSeconds(45);

    /**
     * Maximum response body the client buffers in memory. Default 64 MiB.
     */
    private int maxInMemorySize = 64 * 1024 * 1024;

    /**
     * Optional caller identifier added as the {@code X-Tenant-Id}
     * header on every request.
     */
    @Nullable
    private String tenantId;

    private final Webhook webhook = new Webhook();

    @Nullable
    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(@Nullable String baseUrl) {
        this.baseUrl = baseUrl;
    }

    @Nullable
    public String getApiKey() {
        return apiKey;
    }

    public void setApiKey(@Nullable String apiKey) {
        this.apiKey = apiKey;
    }

    public Duration getTimeout() {
        return timeout;
    }

    public void setTimeout(Duration timeout) {
        this.timeout = timeout;
    }

    public int getMaxAttempts() {
        return maxAttempts;
    }

    public void setMaxAttempts(int maxAttempts) {
        this.maxAttempts = maxAttempts;
    }

    public Duration getRetryMinBackoff() {
        return retryMinBackoff;
    }

    public void setRetryMinBackoff(Duration retryMinBackoff) {
        this.retryMinBackoff = retryMinBackoff;
    }

    public int getMaxConnections() {
        return maxConnections;
    }

    public void setMaxConnections(int maxConnections) {
        this.maxConnections = maxConnections;
    }

    public Duration getPendingAcquireTimeout() {
        return pendingAcquireTimeout;
    }

    public void setPendingAcquireTimeout(Duration pendingAcquireTimeout) {
        this.pendingAcquireTimeout = pendingAcquireTimeout;
    }

    public int getMaxInMemorySize() {
        return maxInMemorySize;
    }

    public void setMaxInMemorySize(int maxInMemorySize) {
        this.maxInMemorySize = maxInMemorySize;
    }

    @Nullable
    public String getTenantId() {
        return tenantId;
    }

    public void setTenantId(@Nullable String tenantId) {
        this.tenantId = tenantId;
    }

    public Webhook getWebhook() {
        return webhook;
    }

    /** Nested webhook settings. */
    public static class Webhook {
        /**
         * HMAC-SHA256 secret used to verify inbound webhook signatures.
         * When set, the starter publishes a {@link com.firefly.flydocs.sdk.webhook.WebhookVerifier}
         * bean and registers a {@link FlydocsWebhookArgumentResolver} so
         * {@code @FlydocsWebhook} parameters resolve correctly.
         */
        @Nullable
        private String secret;

        @Nullable
        public String getSecret() {
            return secret;
        }

        public void setSecret(@Nullable String secret) {
            this.secret = secret;
        }
    }
}
