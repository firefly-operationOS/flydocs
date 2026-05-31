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

package com.firefly.flydocs.sdk.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * Unified envelope for EDA events and webhook deliveries.
 *
 * <p>One envelope shape shared between the EDA bus and HTTP webhook
 * POSTs. Operators see the same payload over Kafka, Redis, Postgres
 * LISTEN/NOTIFY, and HTTP webhook calls. Event types are
 * dotted snake_case: {@code extraction.submitted},
 * {@code extraction.completed},
 * {@code extraction.post_processing.requested},
 * {@code extraction.post_processing.completed}.</p>
 *
 * <p>{@link #extraction()} carries a current-state snapshot of the
 * resource. {@link #result()} is populated only on
 * {@code extraction.completed} when the terminal status is
 * {@code succeeded}; otherwise {@code null}.</p>
 */
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public record EventEnvelope(
        @JsonProperty("event_id") String eventId,
        @JsonProperty("event_type") String eventType,
        @JsonProperty("version") String version,
        @JsonProperty("occurred_at") OffsetDateTime occurredAt,
        @JsonProperty("correlation_id") @Nullable String correlationId,
        @JsonProperty("tenant_id") @Nullable String tenantId,
        @JsonProperty("extraction") Extraction extraction,
        @JsonProperty("result") @Nullable ExtractionResult result,
        @JsonProperty("metadata") Map<String, Object> metadata) {

    public EventEnvelope {
        metadata = metadata == null ? Map.of() : Map.copyOf(metadata);
    }

    public static final String TYPE_EXTRACTION_SUBMITTED = "extraction.submitted";
    public static final String TYPE_EXTRACTION_COMPLETED = "extraction.completed";
    public static final String TYPE_EXTRACTION_POST_PROCESSING_REQUESTED = "extraction.post_processing.requested";
    public static final String TYPE_EXTRACTION_POST_PROCESSING_COMPLETED = "extraction.post_processing.completed";
}
