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
import org.jspecify.annotations.Nullable;

/**
 * Response-side summary for one input file.
 *
 * <p>{@code matched_type} is the *final assignment* (caller's
 * {@code expected_type} when one was given, classifier verdict otherwise;
 * {@code null} when neither resolved). Was {@code document_type} in v0.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record FileSummary(
        @JsonProperty("filename") String filename,
        @JsonProperty("media_type") String mediaType,
        @JsonProperty("page_count") int pageCount,
        @JsonProperty("bytes") int bytes,
        @JsonProperty("matched_type") @Nullable String matchedType,
        @JsonProperty("classification") @Nullable ClassificationInfo classification) {
}
