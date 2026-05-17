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
import org.jspecify.annotations.Nullable;

/** Response body for {@code GET /api/v1/jobs/{id}} and {@code DELETE /api/v1/jobs/{id}}. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record JobStatusResponse(
        @JsonProperty("job_id") String jobId,
        JobStatus status,
        @JsonProperty("submitted_at") OffsetDateTime submittedAt,
        @JsonProperty("started_at") @Nullable OffsetDateTime startedAt,
        @JsonProperty("finished_at") @Nullable OffsetDateTime finishedAt,
        int attempts,
        @JsonProperty("error_code") @Nullable String errorCode,
        @JsonProperty("error_message") @Nullable String errorMessage,
        @JsonProperty("bbox_refine_status") @Nullable String bboxRefineStatus,
        @JsonProperty("bbox_refine_attempts") int bboxRefineAttempts,
        @JsonProperty("bbox_refine_started_at") @Nullable OffsetDateTime bboxRefineStartedAt,
        @JsonProperty("bbox_refine_finished_at") @Nullable OffsetDateTime bboxRefineFinishedAt,
        @JsonProperty("bbox_refine_error_code") @Nullable String bboxRefineErrorCode,
        @JsonProperty("bbox_refine_error_message") @Nullable String bboxRefineErrorMessage) {

    /** Terminal status (no more state transitions). */
    public boolean isTerminal() {
        return status == JobStatus.SUCCEEDED
                || status == JobStatus.PARTIAL_SUCCEEDED
                || status == JobStatus.FAILED
                || status == JobStatus.CANCELLED;
    }
}
