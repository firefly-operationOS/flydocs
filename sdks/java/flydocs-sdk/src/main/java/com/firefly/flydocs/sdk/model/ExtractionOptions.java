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
import org.jspecify.annotations.Nullable;

/**
 * Per-request knobs.
 *
 * <p>v1 reshape: {@code escalation_threshold} + {@code escalation_model}
 * collapsed into a single {@link EscalationConfig} on {@link #escalation()};
 * {@code null} when judge-escalation is off. {@code transformations} is now
 * a typed {@link Transformation} list (sealed union) rather than raw maps.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractionOptions(
        @JsonProperty("model") @Nullable String model,
        @JsonProperty("language_hint") @Nullable String languageHint,
        @JsonProperty("return_bboxes") boolean returnBboxes,
        @JsonProperty("declared_media_type") @Nullable String declaredMediaType,
        @JsonProperty("stages") StageToggles stages,
        @JsonProperty("escalation") @Nullable EscalationConfig escalation,
        @JsonProperty("transformations") List<Transformation> transformations) {

    public ExtractionOptions {
        if (stages == null) {
            stages = StageToggles.defaults();
        }
        transformations = transformations == null ? List.of() : List.copyOf(transformations);
    }

    public static ExtractionOptions defaults() {
        return new ExtractionOptions(null, null, true, null, StageToggles.defaults(), null, List.of());
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private @Nullable String model;
        private @Nullable String languageHint;
        private boolean returnBboxes = true;
        private @Nullable String declaredMediaType;
        private StageToggles stages = StageToggles.defaults();
        private @Nullable EscalationConfig escalation;
        private final List<Transformation> transformations = new ArrayList<>();

        public Builder model(@Nullable String v) { this.model = v; return this; }
        public Builder languageHint(@Nullable String v) { this.languageHint = v; return this; }
        public Builder returnBboxes(boolean v) { this.returnBboxes = v; return this; }
        public Builder declaredMediaType(@Nullable String v) { this.declaredMediaType = v; return this; }
        public Builder stages(StageToggles v) { this.stages = v; return this; }
        public Builder escalation(@Nullable EscalationConfig v) { this.escalation = v; return this; }
        public Builder escalation(double threshold, String escModel) {
            this.escalation = new EscalationConfig(threshold, escModel);
            return this;
        }
        public Builder transformation(Transformation t) { this.transformations.add(t); return this; }

        public ExtractionOptions build() {
            return new ExtractionOptions(
                    model, languageHint, returnBboxes, declaredMediaType,
                    stages, escalation, List.copyOf(transformations));
        }
    }
}
