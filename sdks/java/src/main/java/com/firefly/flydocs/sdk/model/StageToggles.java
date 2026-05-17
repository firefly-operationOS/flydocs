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

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Opt-in switches for every optional pipeline stage.
 *
 * <p>The multimodal extractor is always on; everything else is opt-in.
 * Defaults match the service-side {@code StageToggles} defaults so an
 * empty {@code StageToggles} produces the same behaviour as omitting
 * the field on the wire.</p>
 *
 * <p>Use the {@link #builder() builder} for readable construction:</p>
 *
 * <pre>{@code
 * StageToggles s = StageToggles.builder()
 *         .judge(true)
 *         .bboxRefine(true)
 *         .build();
 * }</pre>
 */
public record StageToggles(
        boolean splitter,
        boolean classifier,
        @JsonProperty("field_validation") boolean fieldValidation,
        @JsonProperty("visual_authenticity") boolean visualAuthenticity,
        @JsonProperty("content_authenticity") boolean contentAuthenticity,
        boolean judge,
        @JsonProperty("rule_engine") boolean ruleEngine,
        @JsonProperty("judge_escalation") boolean judgeEscalation,
        @JsonProperty("bbox_refine") boolean bboxRefine,
        boolean transform) {

    /** Service-default toggles (classifier + field_validation on, everything else off). */
    public static StageToggles defaults() {
        return new StageToggles(false, true, true, false, false, false, false, false, false, false);
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. Every field defaults to the service's default. */
    public static final class Builder {
        private boolean splitter = false;
        private boolean classifier = true;
        private boolean fieldValidation = true;
        private boolean visualAuthenticity = false;
        private boolean contentAuthenticity = false;
        private boolean judge = false;
        private boolean ruleEngine = false;
        private boolean judgeEscalation = false;
        private boolean bboxRefine = false;
        private boolean transform = false;

        public Builder splitter(boolean v) { this.splitter = v; return this; }
        public Builder classifier(boolean v) { this.classifier = v; return this; }
        public Builder fieldValidation(boolean v) { this.fieldValidation = v; return this; }
        public Builder visualAuthenticity(boolean v) { this.visualAuthenticity = v; return this; }
        public Builder contentAuthenticity(boolean v) { this.contentAuthenticity = v; return this; }
        public Builder judge(boolean v) { this.judge = v; return this; }
        public Builder ruleEngine(boolean v) { this.ruleEngine = v; return this; }
        public Builder judgeEscalation(boolean v) { this.judgeEscalation = v; return this; }
        public Builder bboxRefine(boolean v) { this.bboxRefine = v; return this; }
        public Builder transform(boolean v) { this.transform = v; return this; }

        public StageToggles build() {
            return new StageToggles(
                    splitter, classifier, fieldValidation,
                    visualAuthenticity, contentAuthenticity,
                    judge, ruleEngine, judgeEscalation,
                    bboxRefine, transform);
        }
    }
}
