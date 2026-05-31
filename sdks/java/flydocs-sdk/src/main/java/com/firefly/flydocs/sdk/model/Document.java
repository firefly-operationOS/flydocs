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
import java.util.List;
import org.jspecify.annotations.Nullable;

/**
 * Response-side: one extracted document instance.
 *
 * <p>v1 renames: {@code document_type} → {@code type}; the inner
 * {@code fields} (which contained groups, confusingly) becomes
 * {@code field_groups}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record Document(
        @JsonProperty("type") String type,
        @JsonProperty("source_file") @Nullable String sourceFile,
        @JsonProperty("missing") boolean missing,
        @JsonProperty("pages") List<Integer> pages,
        @JsonProperty("confidence") double confidence,
        @JsonProperty("description") @Nullable String description,
        @JsonProperty("notes") @Nullable String notes,
        @JsonProperty("field_groups") List<ExtractedFieldGroup> fieldGroups,
        @JsonProperty("authenticity") DocumentAuthenticity authenticity) {

    public Document {
        pages = pages == null ? List.of() : List.copyOf(pages);
        fieldGroups = fieldGroups == null ? List.of() : List.copyOf(fieldGroups);
        if (authenticity == null) {
            authenticity = DocumentAuthenticity.empty();
        }
    }
}
