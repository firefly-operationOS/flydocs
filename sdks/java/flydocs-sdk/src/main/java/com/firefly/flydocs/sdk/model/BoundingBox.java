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
import org.jspecify.annotations.Nullable;

/**
 * Normalised rectangle on a single page.
 *
 * <p>All values are in {@code [0, 1]}; (0, 0) is the top-left of the
 * rendered page. Absence is signalled by a {@code null} bbox on the
 * consuming field — v1 dropped the synthetic empty-placeholder.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record BoundingBox(
        @JsonProperty("xmin") double xmin,
        @JsonProperty("ymin") double ymin,
        @JsonProperty("xmax") double xmax,
        @JsonProperty("ymax") double ymax,
        @JsonProperty("quality") @Nullable BboxQuality quality,
        @JsonProperty("quality_score") double qualityScore,
        @JsonProperty("source") @Nullable BboxSource source,
        @JsonProperty("refinement_confidence") @Nullable Double refinementConfidence) {
}
