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
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * Request body for {@code POST /api/v1/extractions} (async).
 *
 * <p>Superset of {@link ExtractionRequest} — adds the optional
 * {@code callback_url} (webhook on terminal status) and a free-form
 * {@code metadata} bag echoed back on the webhook envelope.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record SubmitExtractionRequest(
        @JsonProperty("intention") String intention,
        @JsonProperty("files") List<FileInput> files,
        @JsonProperty("document_types") List<DocumentTypeSpec> documentTypes,
        @JsonProperty("rules") List<RuleSpec> rules,
        @JsonProperty("options") ExtractionOptions options,
        @JsonProperty("callback_url") @Nullable String callbackUrl,
        @JsonProperty("metadata") Map<String, Object> metadata) {

    public SubmitExtractionRequest {
        if (intention == null) {
            intention = "Extract structured data from the document.";
        }
        files = List.copyOf(files);
        documentTypes = List.copyOf(documentTypes);
        rules = rules == null ? List.of() : List.copyOf(rules);
        if (options == null) {
            options = ExtractionOptions.defaults();
        }
        metadata = metadata == null ? Map.of() : Map.copyOf(metadata);
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
        private @Nullable String callbackUrl;
        private final Map<String, Object> metadata = new HashMap<>();

        public Builder intention(String s) { this.intention = s; return this; }
        public Builder addFile(FileInput f) { this.files.add(f); return this; }
        public Builder addDocumentType(DocumentTypeSpec d) { this.documentTypes.add(d); return this; }
        public Builder addRule(RuleSpec r) { this.rules.add(r); return this; }
        public Builder options(ExtractionOptions o) { this.options = o; return this; }
        public Builder callbackUrl(String url) { this.callbackUrl = url; return this; }
        public Builder metadata(String key, Object value) { this.metadata.put(key, value); return this; }
        public Builder metadata(Map<String, Object> m) { this.metadata.putAll(m); return this; }

        public SubmitExtractionRequest build() {
            return new SubmitExtractionRequest(
                    intention,
                    List.copyOf(files),
                    List.copyOf(documentTypes),
                    List.copyOf(rules),
                    options,
                    callbackUrl,
                    Map.copyOf(metadata));
        }
    }
}
