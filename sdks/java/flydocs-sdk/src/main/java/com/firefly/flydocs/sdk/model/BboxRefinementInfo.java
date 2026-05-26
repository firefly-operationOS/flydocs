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

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.OffsetDateTime;
import org.jspecify.annotations.Nullable;

/**
 * Lifecycle info for the bbox-refinement post-processing leg.
 *
 * <p>v1 collapses the flat {@code bbox_refine_*} columns under
 * {@link Extraction#postProcessing()}.{@link PostProcessing#bboxRefinement()}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record BboxRefinementInfo(
        @JsonProperty("status") PostProcessingStatus status,
        @JsonProperty("started_at") @Nullable OffsetDateTime startedAt,
        @JsonProperty("finished_at") @Nullable OffsetDateTime finishedAt,
        @JsonProperty("attempts") int attempts,
        @JsonProperty("error") @Nullable ExtractionError error) {
}
