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

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.jspecify.annotations.Nullable;

/**
 * A caller-declared "the parts must sum to a whole" constraint on an LLM
 * transformation. Domain-agnostic: name the per-row numeric {@code shareField}
 * and the {@code total} those shares must add up to (e.g. {@code 100} for
 * percentages). flydocs enforces it deterministically after the LLM call -- on
 * an over-sum it repairs (drops the least-trustworthy rows until it fits) or
 * warns, per {@code onViolation}. Leave {@code total}/{@code tolerance}/
 * {@code onViolation} null to take the service defaults (100 / 0.5 / repair).
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record PartsOfWholeInvariant(
        @JsonProperty("share_field") String shareField,
        @JsonProperty("total") @Nullable Double total,
        @JsonProperty("tolerance") @Nullable Double tolerance,
        @JsonProperty("on_violation") @Nullable String onViolation) {}
