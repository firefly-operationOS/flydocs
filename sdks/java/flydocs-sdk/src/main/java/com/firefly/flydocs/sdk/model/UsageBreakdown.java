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
import java.util.Map;

/** Aggregated token usage and cost across every LLM call of one request. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record UsageBreakdown(
        @JsonProperty("total_input_tokens") long totalInputTokens,
        @JsonProperty("total_output_tokens") long totalOutputTokens,
        @JsonProperty("total_tokens") long totalTokens,
        @JsonProperty("total_cost_usd") double totalCostUsd,
        @JsonProperty("total_requests") long totalRequests,
        @JsonProperty("total_latency_ms") double totalLatencyMs,
        @JsonProperty("record_count") long recordCount,
        @JsonProperty("cache_creation_tokens") long cacheCreationTokens,
        @JsonProperty("cache_read_tokens") long cacheReadTokens,
        @JsonProperty("by_agent") Map<String, Map<String, Object>> byAgent,
        @JsonProperty("by_model") Map<String, Map<String, Object>> byModel) {

    public UsageBreakdown {
        byAgent = byAgent == null ? Map.of() : Map.copyOf(byAgent);
        byModel = byModel == null ? Map.of() : Map.copyOf(byModel);
    }
}
