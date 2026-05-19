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
import org.jspecify.annotations.Nullable;

/**
 * One sub-field declared inside an {@link FieldType#ARRAY} field's {@code items} list.
 *
 * <p>The JSON keys are camelCase ({@code fieldName}, {@code fieldType}) to
 * match the service's contract — Jackson maps them via {@link JsonProperty}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record FieldItem(
        @JsonProperty("fieldName") String fieldName,
        @JsonProperty("fieldDescription") String fieldDescription,
        @JsonProperty("fieldType") FieldType fieldType,
        @Nullable String pattern,
        @Nullable StandardFormat format,
        @Nullable List<Object> enumValues,
        @Nullable Double minimum,
        @Nullable Double maximum,
        @JsonProperty("standard_validators") List<StandardValidatorSpec> standardValidators) {

    public FieldItem {
        if (fieldDescription == null) fieldDescription = "";
        if (fieldType == null) fieldType = FieldType.STRING;
        if (standardValidators == null) standardValidators = List.of();
    }

    /** Concise factory: {@code FieldItem.of("amount", FieldType.NUMBER)}. */
    public static FieldItem of(String name, FieldType type) {
        return new FieldItem(name, "", type, null, null, null, null, null, List.of());
    }
}
