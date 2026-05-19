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
import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;
import java.util.List;

/**
 * Where a {@link RuleSpec} sources its input from.
 *
 * <p>Sealed union with three variants:</p>
 *
 * <ul>
 *   <li>{@link FieldParent} — one or more fields on a known document type.</li>
 *   <li>{@link ValidatorParent} — the outcome of a named validator.</li>
 *   <li>{@link RuleParent.RuleRef} — the resolved output of an upstream rule.</li>
 * </ul>
 *
 * <p>Jackson routes serialisation via the {@code parentType} discriminator
 * the service expects on the wire.</p>
 */
@JsonTypeInfo(
        use = JsonTypeInfo.Id.NAME,
        include = JsonTypeInfo.As.PROPERTY,
        property = "parentType")
@JsonSubTypes({
        @JsonSubTypes.Type(value = RuleParent.FieldParent.class, name = "field"),
        @JsonSubTypes.Type(value = RuleParent.ValidatorParent.class, name = "validator"),
        @JsonSubTypes.Type(value = RuleParent.RuleRef.class, name = "rule"),
})
public sealed interface RuleParent
        permits RuleParent.FieldParent, RuleParent.ValidatorParent, RuleParent.RuleRef {

    /** Reference to one or more fields on a known document type. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record FieldParent(
            @JsonProperty("documentType") String documentType,
            @JsonProperty("fieldNames") List<String> fieldNames)
            implements RuleParent {

        public FieldParent {
            fieldNames = List.copyOf(fieldNames);
        }
    }

    /** Reference to a named validator's outcome on a known document type. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record ValidatorParent(
            @JsonProperty("documentType") String documentType,
            @JsonProperty("validatorName") String validatorName)
            implements RuleParent {
    }

    /** Reference to another rule's resolved output. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record RuleRef(@JsonProperty("ruleId") String ruleId) implements RuleParent {
    }
}
