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
import java.util.ArrayList;
import java.util.List;
import org.jspecify.annotations.Nullable;

/**
 * Request body for {@code POST /api/v1/extract}.
 *
 * <p>v1 renames: {@code documents} → {@code files}; {@code docs}
 * → {@code document_types}; {@code request_id} removed (the server
 * generates a prefixed {@code ext_…} id).</p>
 *
 * <pre>{@code
 * ExtractionRequest req = ExtractionRequest.builder()
 *         .addFile(FileInput.ofPath(Path.of("invoice.pdf")))
 *         .addDocumentType(DocumentTypeSpec.builder("invoice")
 *                 .addFieldGroup("totals",
 *                         Field.required("total_amount", FieldType.NUMBER),
 *                         Field.required("currency",     FieldType.STRING))
 *                 .build())
 *         .options(ExtractionOptions.builder()
 *                 .stages(StageToggles.builder().judge(true).bboxRefine(true).build())
 *                 .build())
 *         .build();
 * }</pre>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractionRequest(
        @JsonProperty("intention") String intention,
        @JsonProperty("files") List<FileInput> files,
        @JsonProperty("document_types") List<DocumentTypeSpec> documentTypes,
        @JsonProperty("rules") List<RuleSpec> rules,
        @JsonProperty("options") ExtractionOptions options) {

    public ExtractionRequest {
        if (intention == null) {
            intention = "Extract structured data from the document.";
        }
        files = List.copyOf(files);
        documentTypes = List.copyOf(documentTypes);
        rules = rules == null ? List.of() : List.copyOf(rules);
        if (options == null) {
            options = ExtractionOptions.defaults();
        }
    }

    /** Concise factory for the simplest case. */
    public static ExtractionRequest of(List<FileInput> files, List<DocumentTypeSpec> documentTypes) {
        return new ExtractionRequest(null, files, documentTypes, List.of(), ExtractionOptions.defaults());
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private @Nullable String intention;
        private final List<FileInput> files = new ArrayList<>();
        private final List<DocumentTypeSpec> documentTypes = new ArrayList<>();
        private final List<RuleSpec> rules = new ArrayList<>();
        private ExtractionOptions options = ExtractionOptions.defaults();

        public Builder intention(String s) { this.intention = s; return this; }
        public Builder addFile(FileInput f) { this.files.add(f); return this; }
        public Builder addDocumentType(DocumentTypeSpec d) { this.documentTypes.add(d); return this; }
        public Builder addRule(RuleSpec r) { this.rules.add(r); return this; }
        public Builder options(ExtractionOptions o) { this.options = o; return this; }

        public ExtractionRequest build() {
            return new ExtractionRequest(
                    intention,
                    List.copyOf(files),
                    List.copyOf(documentTypes),
                    List.copyOf(rules),
                    options);
        }
    }
}
