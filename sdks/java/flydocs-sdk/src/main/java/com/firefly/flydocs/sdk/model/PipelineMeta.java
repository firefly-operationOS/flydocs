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
import java.util.List;
import org.jspecify.annotations.Nullable;

/**
 * Pipeline-level instrumentation metadata for one extraction.
 *
 * <p>v1 nests model / latency / trace / errors / escalation / usage under
 * a single {@code pipeline} block so business data isn't drowned in
 * instrumentation.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record PipelineMeta(
        @JsonProperty("model") String model,
        @JsonProperty("latency_ms") int latencyMs,
        @JsonProperty("trace") List<TraceEntry> trace,
        @JsonProperty("errors") List<PipelineError> errors,
        @JsonProperty("escalation") @Nullable EscalationInfo escalation,
        @JsonProperty("usage") @Nullable UsageBreakdown usage) {

    public PipelineMeta {
        trace = trace == null ? List.of() : List.copyOf(trace);
        errors = errors == null ? List.of() : List.copyOf(errors);
    }
}
