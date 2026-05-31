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
 * One expected document type the caller is submitting fields for.
 *
 * <p>v1 collapses the v0 {@code docType.documentType} stutter into
 * {@code id} at the top level, and moves visual checks out of the
 * v0 {@code validators.visual[]} envelope onto a flat
 * {@code visual_checks[]} list.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record DocumentTypeSpec(
        @JsonProperty("id") String id,
        @JsonProperty("description") @Nullable String description,
        @JsonProperty("country") @Nullable String country,
        @JsonProperty("field_groups") List<FieldGroup> fieldGroups,
        @JsonProperty("visual_checks") List<VisualCheck> visualChecks) {

    public DocumentTypeSpec {
        fieldGroups = List.copyOf(fieldGroups);
        visualChecks = visualChecks == null ? List.of() : List.copyOf(visualChecks);
    }

    public static Builder builder(String id) {
        return new Builder(id);
    }

    /** Fluent builder. */
    public static final class Builder {
        private final String id;
        private @Nullable String description;
        private @Nullable String country;
        private final List<FieldGroup> fieldGroups = new ArrayList<>();
        private final List<VisualCheck> visualChecks = new ArrayList<>();

        Builder(String id) {
            this.id = id;
        }

        public Builder description(@Nullable String v) { this.description = v; return this; }
        public Builder country(@Nullable String v) { this.country = v; return this; }
        public Builder addFieldGroup(FieldGroup g) { this.fieldGroups.add(g); return this; }
        public Builder addFieldGroup(String name, Field... fields) {
            this.fieldGroups.add(FieldGroup.of(name, fields));
            return this;
        }
        public Builder addVisualCheck(VisualCheck v) { this.visualChecks.add(v); return this; }
        public Builder addVisualCheck(String name, String description) {
            this.visualChecks.add(new VisualCheck(name, description));
            return this;
        }

        public DocumentTypeSpec build() {
            return new DocumentTypeSpec(
                    id, description, country,
                    List.copyOf(fieldGroups),
                    List.copyOf(visualChecks));
        }
    }
}
