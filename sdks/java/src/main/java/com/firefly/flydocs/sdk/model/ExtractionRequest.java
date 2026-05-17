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
import java.util.UUID;
import org.jspecify.annotations.Nullable;

/**
 * Request body for {@code POST /api/v1/extract}.
 *
 * <p>The deeply nested schema shapes ({@code docs}, {@code rules},
 * {@code options}) are intentionally typed as raw maps. Callers build
 * them directly; the SDK doesn't try to mirror every field of the
 * service-side DTO library. See
 * {@code docs/api-reference.md} for the full structure.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractionRequest(
        @JsonProperty("request_id") UUID requestId,
        String intention,
        List<DocumentInput> documents,
        List<Map<String, Object>> docs,
        List<Map<String, Object>> rules,
        Map<String, Object> options) {

    /**
     * Convenience constructor that fills in a random request id and the
     * default intention. Callers can still build the record directly when
     * they want to pin specific values for replay or testing.
     */
    public static ExtractionRequest of(List<DocumentInput> documents, List<Map<String, Object>> docs) {
        return new ExtractionRequest(
                UUID.randomUUID(),
                "Extract structured data from the document.",
                documents,
                docs,
                List.of(),
                Map.of());
    }

    /**
     * Compact constructor — null-tolerant for the optional list / map
     * fields so callers can hand us {@code null} for "use defaults".
     */
    public ExtractionRequest(
            @Nullable UUID requestId,
            @Nullable String intention,
            List<DocumentInput> documents,
            List<Map<String, Object>> docs,
            @Nullable List<Map<String, Object>> rules,
            @Nullable Map<String, Object> options) {
        this.requestId = requestId == null ? UUID.randomUUID() : requestId;
        this.intention = intention == null ? "Extract structured data from the document." : intention;
        this.documents = List.copyOf(documents);
        this.docs = List.copyOf(docs);
        this.rules = rules == null ? List.of() : List.copyOf(rules);
        this.options = options == null ? Map.of() : Map.copyOf(options);
    }
}
