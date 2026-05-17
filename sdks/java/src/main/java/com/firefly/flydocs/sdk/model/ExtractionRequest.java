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
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.jspecify.annotations.Nullable;

/**
 * Request body for {@code POST /api/v1/extract}.
 *
 * <p>Every nested shape is now a first-class type
 * ({@link DocSpec}, {@link RuleSpec}, {@link ExtractionOptions}).
 * Use {@link #builder()} for readable construction:</p>
 *
 * <pre>{@code
 * ExtractionRequest req = ExtractionRequest.builder()
 *         .addDocument(DocumentInput.ofPath(Path.of("invoice.pdf")))
 *         .addDocSpec(DocSpec.builder("invoice")
 *                 .description("Vendor invoice")
 *                 .addFieldGroup("totals",
 *                         FieldSpec.required("total_amount", FieldType.NUMBER),
 *                         FieldSpec.required("currency",     FieldType.STRING))
 *                 .build())
 *         .options(ExtractionOptions.builder()
 *                 .stages(StageToggles.builder().judge(true).bboxRefine(true).build())
 *                 .build())
 *         .build();
 * }</pre>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractionRequest(
        @JsonProperty("request_id") UUID requestId,
        String intention,
        List<DocumentInput> documents,
        List<DocSpec> docs,
        List<RuleSpec> rules,
        ExtractionOptions options) {

    public ExtractionRequest {
        if (requestId == null) requestId = UUID.randomUUID();
        if (intention == null) intention = "Extract structured data from the document.";
        documents = List.copyOf(documents);
        docs = List.copyOf(docs);
        rules = rules == null ? List.of() : List.copyOf(rules);
        if (options == null) options = ExtractionOptions.defaults();
    }

    /** Concise factory for the simplest case. */
    public static ExtractionRequest of(List<DocumentInput> documents, List<DocSpec> docs) {
        return new ExtractionRequest(null, null, documents, docs, List.of(), ExtractionOptions.defaults());
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private @Nullable UUID requestId;
        private @Nullable String intention;
        private final List<DocumentInput> documents = new ArrayList<>();
        private final List<DocSpec> docs = new ArrayList<>();
        private final List<RuleSpec> rules = new ArrayList<>();
        private ExtractionOptions options = ExtractionOptions.defaults();

        public Builder requestId(UUID id) { this.requestId = id; return this; }
        public Builder intention(String s) { this.intention = s; return this; }
        public Builder addDocument(DocumentInput d) { this.documents.add(d); return this; }
        public Builder addDocSpec(DocSpec d) { this.docs.add(d); return this; }
        public Builder addRule(RuleSpec r) { this.rules.add(r); return this; }
        public Builder options(ExtractionOptions o) { this.options = o; return this; }

        public ExtractionRequest build() {
            return new ExtractionRequest(
                    requestId, intention,
                    List.copyOf(documents),
                    List.copyOf(docs),
                    List.copyOf(rules),
                    options);
        }
    }
}
