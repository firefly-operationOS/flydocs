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
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.jspecify.annotations.Nullable;

/**
 * Response body for {@code POST /api/v1/extract} and the {@code result}
 * field of {@link JobResult}.
 *
 * <p>Top-level identity / metadata is typed strictly; deeply nested
 * per-document / per-field shapes stay as raw maps so the SDK keeps
 * working when the service adds new attributes without a coordinated
 * release.</p>
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ExtractionResult(
        @JsonProperty("request_id") UUID requestId,
        List<Map<String, Object>> files,
        List<Map<String, Object>> documents,
        @JsonProperty("additional_documents") List<Map<String, Object>> additionalDocuments,
        @JsonProperty("rule_results") List<Map<String, Object>> ruleResults,
        @JsonProperty("request_transformations") List<Map<String, Object>> requestTransformations,
        String model,
        @JsonProperty("latency_ms") int latencyMs,
        @JsonProperty("pipeline_errors") List<Map<String, Object>> pipelineErrors,
        @Nullable Map<String, Object> escalation,
        @Nullable Map<String, Object> usage,
        List<Map<String, Object>> trace) {
}
