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
import java.util.UUID;
import org.jspecify.annotations.Nullable;

/** Deterministic deduplication of an array field group's rows. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record EntityResolutionTransformation(
        @JsonProperty("id") String id,
        @JsonProperty("target_group") String targetGroup,
        @JsonProperty("output_group") @Nullable String outputGroup,
        @JsonProperty("scope") TransformationScope scope,
        @JsonProperty("match_by") List<String> matchBy,
        @JsonProperty("min_shared_tokens") int minSharedTokens) implements Transformation {

    public EntityResolutionTransformation {
        if (id == null) {
            id = UUID.randomUUID().toString();
        }
        if (scope == null) {
            scope = TransformationScope.TASK;
        }
        matchBy = List.copyOf(matchBy);
        if (minSharedTokens < 1) {
            minSharedTokens = 2;
        }
    }
}
