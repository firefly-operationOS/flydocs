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
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * Request body for {@code POST /api/v1/jobs}.
 *
 * <p>Superset of {@link ExtractionRequest} — adds an optional
 * {@code callback_url} the service will POST the terminal-status webhook
 * to, plus a free-form {@code metadata} bag echoed back on the webhook
 * payload.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record SubmitJobRequest(
        String intention,
        List<DocumentInput> documents,
        List<Map<String, Object>> docs,
        List<Map<String, Object>> rules,
        Map<String, Object> options,
        @JsonProperty("callback_url") @Nullable String callbackUrl,
        Map<String, Object> metadata) {

    /** Compact constructor — defaults the lists / maps the same way as {@link ExtractionRequest}. */
    public SubmitJobRequest(
            @Nullable String intention,
            List<DocumentInput> documents,
            List<Map<String, Object>> docs,
            @Nullable List<Map<String, Object>> rules,
            @Nullable Map<String, Object> options,
            @Nullable String callbackUrl,
            @Nullable Map<String, Object> metadata) {
        this.intention = intention == null ? "Extract structured data from the document." : intention;
        this.documents = List.copyOf(documents);
        this.docs = List.copyOf(docs);
        this.rules = rules == null ? List.of() : List.copyOf(rules);
        this.options = options == null ? Map.of() : Map.copyOf(options);
        this.callbackUrl = callbackUrl;
        this.metadata = metadata == null ? Map.of() : Map.copyOf(metadata);
    }
}
