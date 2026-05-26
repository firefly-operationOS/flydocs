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
import org.jspecify.annotations.Nullable;

/**
 * One field the caller wants extracted.
 *
 * <p>Top-level fields use {@code name} / {@code description} / {@code type}
 * on the wire (not the {@code fieldName} form used by {@link FieldItem}).
 * Use {@link #builder() the builder} for anything beyond a one-line declaration.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record FieldSpec(
        @JsonProperty("name") String name,
        @JsonProperty("description") String description,
        @JsonProperty("type") FieldType type,
        boolean required,
        @Nullable String pattern,
        @Nullable StandardFormat format,
        @Nullable List<Object> enumValues,
        @Nullable Double minimum,
        @Nullable Double maximum,
        @Nullable List<FieldItem> items,
        @JsonProperty("standard_validators") List<StandardValidatorSpec> standardValidators) {

    public FieldSpec {
        if (description == null) description = "";
        if (type == null) type = FieldType.STRING;
        if (standardValidators == null) standardValidators = List.of();
    }

    /** Concise factory: {@code FieldSpec.of("total", FieldType.NUMBER)}. */
    public static FieldSpec of(String name, FieldType type) {
        return new FieldSpec(name, "", type, false, null, null, null, null, null, null, List.of());
    }

    /** Concise factory: {@code FieldSpec.required("total", FieldType.NUMBER)}. */
    public static FieldSpec required(String name, FieldType type) {
        return new FieldSpec(name, "", type, true, null, null, null, null, null, null, List.of());
    }

    public static Builder builder(String name) {
        return new Builder(name);
    }

    /** Fluent builder for a single {@link FieldSpec}. */
    public static final class Builder {
        private final String name;
        private String description = "";
        private FieldType type = FieldType.STRING;
        private boolean required = false;
        private @Nullable String pattern;
        private @Nullable StandardFormat format;
        private @Nullable List<Object> enumValues;
        private @Nullable Double minimum;
        private @Nullable Double maximum;
        private @Nullable List<FieldItem> items;
        private final List<StandardValidatorSpec> standardValidators = new ArrayList<>();

        Builder(String name) {
            this.name = name;
        }

        public Builder description(String v) { this.description = v; return this; }
        public Builder type(FieldType v) { this.type = v; return this; }
        public Builder required(boolean v) { this.required = v; return this; }
        public Builder pattern(String v) { this.pattern = v; return this; }
        public Builder format(StandardFormat v) { this.format = v; return this; }
        public Builder enumValues(List<Object> v) { this.enumValues = v; return this; }
        public Builder minimum(Double v) { this.minimum = v; return this; }
        public Builder maximum(Double v) { this.maximum = v; return this; }
        public Builder items(List<FieldItem> v) { this.items = v; return this; }
        public Builder validator(StandardValidatorSpec v) { this.standardValidators.add(v); return this; }

        public FieldSpec build() {
            return new FieldSpec(
                    name, description, type, required,
                    pattern, format, enumValues, minimum, maximum,
                    items, List.copyOf(standardValidators));
        }
    }
}
