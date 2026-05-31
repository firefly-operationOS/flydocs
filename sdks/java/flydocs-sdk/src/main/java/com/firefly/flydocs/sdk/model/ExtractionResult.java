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

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

/**
 * Top-level response shape for {@code POST /api/v1/extract} and the
 * {@code result} field of {@link ExtractionResultEnvelope}.
 *
 * <p>v1 reshape:</p>
 * <ul>
 *   <li>{@code request_id} → {@code id} (prefixed {@code ext_…}).</li>
 *   <li>New {@code status} ({@code success} | {@code partial}).</li>
 *   <li>{@code additional_documents} → {@code discovered_documents}.</li>
 *   <li>Pipeline meta (model, latency, trace, errors, escalation, usage)
 *       nested under {@link #pipeline()}.</li>
 *   <li>Top-level shapes are now strongly typed records (no more raw
 *       {@code Map<String,Object>} columns).</li>
 * </ul>
 */
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractionResult(
        @JsonProperty("id") String id,
        @JsonProperty("status") String status,
        @JsonProperty("files") List<FileSummary> files,
        @JsonProperty("documents") List<Document> documents,
        @JsonProperty("discovered_documents") List<Document> discoveredDocuments,
        @JsonProperty("rule_results") List<RuleResult> ruleResults,
        @JsonProperty("request_transformations") List<ExtractedFieldGroup> requestTransformations,
        @JsonProperty("pipeline") PipelineMeta pipeline) {

    public ExtractionResult {
        files = files == null ? List.of() : List.copyOf(files);
        documents = documents == null ? List.of() : List.copyOf(documents);
        discoveredDocuments = discoveredDocuments == null ? List.of() : List.copyOf(discoveredDocuments);
        ruleResults = ruleResults == null ? List.of() : List.copyOf(ruleResults);
        requestTransformations = requestTransformations == null ? List.of() : List.copyOf(requestTransformations);
    }
}
