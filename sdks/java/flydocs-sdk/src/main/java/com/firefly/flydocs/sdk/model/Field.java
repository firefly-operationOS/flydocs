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
 * One field in a schema. Recursive for arrays and objects.
 *
 * <p>One unified recursive type covers primitives, arrays, and objects.
 * Constraints:</p>
 * <ul>
 *   <li>{@code type=="array"} requires {@code items} (a single {@link Field}
 *       describing the row shape) and forbids {@code fields}.</li>
 *   <li>{@code type=="object"} requires a non-empty {@code fields} list and
 *       forbids {@code items}.</li>
 *   <li>Primitives forbid both.</li>
 * </ul>
 *
 * <p>Validators are declared via {@link ValidatorSpec} on
 * {@code validators[]}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record Field(
        @JsonProperty("name") String name,
        @JsonProperty("description") @Nullable String description,
        @JsonProperty("type") FieldType type,
        @JsonProperty("required") @Nullable Boolean required,
        @JsonProperty("pattern") @Nullable String pattern,
        @JsonProperty("format") @Nullable StandardFormat format,
        @JsonProperty("enum") @Nullable List<Object> enumValues,
        @JsonProperty("minimum") @Nullable Double minimum,
        @JsonProperty("maximum") @Nullable Double maximum,
        @JsonProperty("items") @Nullable Field items,
        @JsonProperty("fields") @Nullable List<Field> fields,
        @JsonProperty("validators") List<ValidatorSpec> validators) {

    public Field {
        if (type == null) {
            type = FieldType.STRING;
        }
        if (validators == null) {
            validators = List.of();
        } else {
            validators = List.copyOf(validators);
        }
        if (fields != null) {
            fields = List.copyOf(fields);
        }
    }

    /** Concise factory: {@code Field.of("currency", FieldType.STRING)}. */
    public static Field of(String name, FieldType type) {
        return new Field(name, null, type, null, null, null, null, null, null, null, null, List.of());
    }

    /** Concise factory: {@code Field.required("total", FieldType.NUMBER)}. */
    public static Field required(String name, FieldType type) {
        return new Field(name, null, type, Boolean.TRUE, null, null, null, null, null, null, null, List.of());
    }

    public static Builder builder(String name) {
        return new Builder(name);
    }

    /** Fluent builder for a single {@link Field}. */
    public static final class Builder {
        private final String name;
        private @Nullable String description;
        private FieldType type = FieldType.STRING;
        private @Nullable Boolean required;
        private @Nullable String pattern;
        private @Nullable StandardFormat format;
        private @Nullable List<Object> enumValues;
        private @Nullable Double minimum;
        private @Nullable Double maximum;
        private @Nullable Field items;
        private @Nullable List<Field> fields;
        private final List<ValidatorSpec> validators = new ArrayList<>();

        Builder(String name) {
            this.name = name;
        }

        public Builder description(@Nullable String v) { this.description = v; return this; }
        public Builder type(FieldType v) { this.type = v; return this; }
        public Builder required(boolean v) { this.required = v; return this; }
        public Builder pattern(@Nullable String v) { this.pattern = v; return this; }
        public Builder format(@Nullable StandardFormat v) { this.format = v; return this; }
        public Builder enumValues(@Nullable List<Object> v) { this.enumValues = v; return this; }
        public Builder minimum(@Nullable Double v) { this.minimum = v; return this; }
        public Builder maximum(@Nullable Double v) { this.maximum = v; return this; }
        /** Set the row shape for arrays. {@code items} is a single recursive {@link Field}. */
        public Builder items(@Nullable Field v) { this.items = v; return this; }
        /** Set the members for objects. {@code fields} is a non-empty list of recursive {@link Field}s. */
        public Builder fields(@Nullable List<Field> v) { this.fields = v; return this; }
        public Builder validator(ValidatorSpec v) { this.validators.add(v); return this; }
        public Builder validators(List<ValidatorSpec> v) {
            this.validators.clear();
            this.validators.addAll(v);
            return this;
        }

        public Field build() {
            return new Field(
                    name, description, type, required, pattern, format,
                    enumValues, minimum, maximum, items, fields,
                    List.copyOf(validators));
        }
    }
}
