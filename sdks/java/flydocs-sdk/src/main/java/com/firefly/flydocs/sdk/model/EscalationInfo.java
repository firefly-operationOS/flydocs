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

/** Audit block for the judge-driven escalation re-run. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record EscalationInfo(
        @JsonProperty("triggered") boolean triggered,
        @JsonProperty("primary_model") @Nullable String primaryModel,
        @JsonProperty("escalation_model") @Nullable String escalationModel,
        @JsonProperty("primary_fail_rate") double primaryFailRate,
        @JsonProperty("escalation_fail_rate") double escalationFailRate,
        @JsonProperty("accepted") boolean accepted) {
}
