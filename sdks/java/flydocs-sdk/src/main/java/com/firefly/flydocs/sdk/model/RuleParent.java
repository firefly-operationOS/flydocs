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
import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;
import java.util.List;

/**
 * Where a {@link RuleSpec} sources its input from.
 *
 * <p>v1 reshapes the discriminator from {@code parentType} to {@code kind}
 * to avoid collision with {@link Field#type()} and {@link RuleOutputSpec#type()}
 * when JSON-Schema-walking tools key on the literal field name.</p>
 *
 * <p>Sealed union with three variants:</p>
 *
 * <ul>
 *   <li>{@link Field} — one or more fields on a known document type.</li>
 *   <li>{@link Validator} — the outcome of a named validator on a document type.</li>
 *   <li>{@link Rule} — the resolved output of another rule.</li>
 * </ul>
 */
@JsonTypeInfo(
        use = JsonTypeInfo.Id.NAME,
        include = JsonTypeInfo.As.PROPERTY,
        property = "kind")
@JsonSubTypes({
        @JsonSubTypes.Type(value = RuleParent.Field.class, name = "field"),
        @JsonSubTypes.Type(value = RuleParent.Validator.class, name = "validator"),
        @JsonSubTypes.Type(value = RuleParent.Rule.class, name = "rule"),
})
public sealed interface RuleParent
        permits RuleParent.Field, RuleParent.Validator, RuleParent.Rule {

    /** Reference to one or more fields on a known document type. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Field(
            @JsonProperty("document_type") String documentType,
            @JsonProperty("fields") List<String> fields)
            implements RuleParent {

        public Field {
            fields = List.copyOf(fields);
        }
    }

    /** Reference to a named validator's outcome on a known document type. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Validator(
            @JsonProperty("document_type") String documentType,
            @JsonProperty("validator") String validator)
            implements RuleParent {
    }

    /** Reference to another rule's resolved output. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record Rule(@JsonProperty("rule") String rule) implements RuleParent {
    }
}
