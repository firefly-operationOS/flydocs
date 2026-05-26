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

/** One expected document type plus its field schema. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record DocSpec(
        @JsonProperty("docType") DocType docType,
        @JsonProperty("fieldGroups") List<FieldGroup> fieldGroups,
        ValidatorsSpec validators) {

    public DocSpec {
        fieldGroups = List.copyOf(fieldGroups);
        if (validators == null) validators = ValidatorsSpec.none();
    }

    public static Builder builder(String documentType) {
        return new Builder(documentType);
    }

    /** Fluent builder. */
    public static final class Builder {
        private final String documentType;
        private String description = "";
        private String country = "";
        private final List<FieldGroup> fieldGroups = new ArrayList<>();
        private ValidatorsSpec validators = ValidatorsSpec.none();

        Builder(String documentType) {
            this.documentType = documentType;
        }

        public Builder description(String v) { this.description = v; return this; }
        public Builder country(String v) { this.country = v; return this; }
        public Builder addFieldGroup(FieldGroup g) { this.fieldGroups.add(g); return this; }
        public Builder addFieldGroup(String name, FieldSpec... fields) {
            this.fieldGroups.add(FieldGroup.of(name, fields));
            return this;
        }
        public Builder validators(ValidatorsSpec v) { this.validators = v; return this; }

        public DocSpec build() {
            return new DocSpec(
                    new DocType(documentType, description, country),
                    List.copyOf(fieldGroups),
                    validators);
        }
    }
}
