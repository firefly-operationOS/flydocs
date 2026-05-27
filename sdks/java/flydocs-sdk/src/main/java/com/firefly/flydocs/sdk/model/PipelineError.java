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

/**
 * Non-fatal per-node failure surfaced on a successful response under
 * {@link PipelineMeta#errors()}. Hard failures come back as
 * {@link com.firefly.flydocs.sdk.error.FlydocsHttpException} instead.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record PipelineError(
        @JsonProperty("node") String node,
        @JsonProperty("code") String code,
        @JsonProperty("message") String message) {
}
