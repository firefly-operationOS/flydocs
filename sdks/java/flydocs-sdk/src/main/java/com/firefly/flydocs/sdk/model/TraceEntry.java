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
import java.time.OffsetDateTime;

/**
 * One node's execution in the pipeline DAG.
 *
 * <p>{@code status} is one of {@code success | failed | skipped}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record TraceEntry(
        @JsonProperty("node") String node,
        @JsonProperty("started_at") OffsetDateTime startedAt,
        @JsonProperty("completed_at") OffsetDateTime completedAt,
        @JsonProperty("latency_ms") double latencyMs,
        @JsonProperty("status") String status) {
}
