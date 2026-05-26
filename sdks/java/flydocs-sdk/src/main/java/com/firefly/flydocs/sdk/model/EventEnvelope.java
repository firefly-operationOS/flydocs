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

package com.firefly.flydocs.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * Body the service POSTs to the configured {@code callback_url} on a
 * job's terminal status. Signed with HMAC-SHA256 in the
 * {@code X-Flydocs-Signature} header; use
 * {@link com.firefly.flydocs.sdk.webhook.WebhookVerifier} to verify.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record JobWebhookPayload(
        @JsonProperty("event_id") String eventId,
        @JsonProperty("event_type") String eventType,
        String version,
        @JsonProperty("job_id") String jobId,
        JobStatus status,
        @JsonProperty("occurred_at") OffsetDateTime occurredAt,
        @JsonProperty("started_at") @Nullable OffsetDateTime startedAt,
        @JsonProperty("finished_at") @Nullable OffsetDateTime finishedAt,
        int attempts,
        @JsonProperty("correlation_id") @Nullable String correlationId,
        @JsonProperty("tenant_id") @Nullable String tenantId,
        Map<String, Object> metadata,
        @Nullable ExtractionResult result,
        @JsonProperty("error_code") @Nullable String errorCode,
        @JsonProperty("error_message") @Nullable String errorMessage) {
}
