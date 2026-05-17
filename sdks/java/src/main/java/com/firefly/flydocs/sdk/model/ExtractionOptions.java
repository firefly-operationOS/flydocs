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
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * Per-request knobs.
 *
 * <p>{@code transformations} stays as a list of raw maps so callers can
 * pick the right discriminated-union shape without the SDK shipping
 * the full transformation type tree. The other fields are typed.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractionOptions(
        @JsonProperty("return_bboxes") boolean returnBboxes,
        @JsonProperty("language_hint") @Nullable String languageHint,
        @Nullable String model,
        @JsonProperty("declared_media_type") @Nullable String declaredMediaType,
        StageToggles stages,
        @JsonProperty("escalation_threshold") @Nullable Double escalationThreshold,
        @JsonProperty("escalation_model") @Nullable String escalationModel,
        List<Map<String, Object>> transformations) {

    public ExtractionOptions {
        if (stages == null) stages = StageToggles.defaults();
        if (transformations == null) transformations = List.of();
    }

    public static ExtractionOptions defaults() {
        return new ExtractionOptions(true, null, null, null, StageToggles.defaults(), null, null, List.of());
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private boolean returnBboxes = true;
        private @Nullable String languageHint;
        private @Nullable String model;
        private @Nullable String declaredMediaType;
        private StageToggles stages = StageToggles.defaults();
        private @Nullable Double escalationThreshold;
        private @Nullable String escalationModel;
        private final List<Map<String, Object>> transformations = new ArrayList<>();

        public Builder returnBboxes(boolean v) { this.returnBboxes = v; return this; }
        public Builder languageHint(@Nullable String v) { this.languageHint = v; return this; }
        public Builder model(@Nullable String v) { this.model = v; return this; }
        public Builder declaredMediaType(@Nullable String v) { this.declaredMediaType = v; return this; }
        public Builder stages(StageToggles v) { this.stages = v; return this; }
        public Builder escalationThreshold(@Nullable Double v) { this.escalationThreshold = v; return this; }
        public Builder escalationModel(@Nullable String v) { this.escalationModel = v; return this; }
        public Builder transformation(Map<String, Object> t) { this.transformations.add(Map.copyOf(t)); return this; }

        public ExtractionOptions build() {
            return new ExtractionOptions(
                    returnBboxes, languageHint, model, declaredMediaType,
                    stages, escalationThreshold, escalationModel,
                    List.copyOf(transformations));
        }
    }
}
