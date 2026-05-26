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

/** Per-rule outcome returned in the response. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record RuleResult(
        @JsonProperty("rule_id") String ruleId,
        @JsonProperty("predicate") String predicate,
        @JsonProperty("output") String output,
        @JsonProperty("summary") @Nullable String summary,
        @JsonProperty("notes") List<String> notes,
        @JsonProperty("human_revision") @Nullable String humanRevision) {

    public RuleResult {
        notes = notes == null ? List.of() : List.copyOf(notes);
    }
}
