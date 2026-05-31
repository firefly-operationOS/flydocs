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
import org.jspecify.annotations.Nullable;

/**
 * Current state snapshot of an async extraction.
 *
 * <p>Response body for {@code POST /api/v1/extractions} (202),
 * {@code GET  /api/v1/extractions/{id}}, and {@code DELETE  /api/v1/extractions/{id}}.</p>
 *
 * <p>v1 simplifies the state machine: {@code queued -> running -> succeeded
 * | failed | cancelled}. The v0 {@code PARTIAL_SUCCEEDED} / {@code REFINING_BBOXES}
 * intermediate states are gone; bbox refinement runs as additive
 * post-processing under {@link #postProcessing()} on a fully-succeeded
 * extraction.</p>
 */
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public record Extraction(
        @JsonProperty("id") String id,
        @JsonProperty("status") ExtractionStatus status,
        @JsonProperty("submitted_at") OffsetDateTime submittedAt,
        @JsonProperty("started_at") @Nullable OffsetDateTime startedAt,
        @JsonProperty("finished_at") @Nullable OffsetDateTime finishedAt,
        @JsonProperty("attempts") int attempts,
        @JsonProperty("error") @Nullable ExtractionError error,
        @JsonProperty("post_processing") @Nullable PostProcessing postProcessing) {

    /** True when no further main-status transition is expected. */
    public boolean isTerminal() {
        return status != null && status.isTerminal();
    }
}
