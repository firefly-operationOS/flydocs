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
 * A named bundle of {@link Field}s the service should extract together.
 *
 * <p>v1 drops the {@code fieldGroup*} prefix stutter: members are just
 * {@code name}, {@code description}, {@code fields}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record FieldGroup(
        @JsonProperty("name") String name,
        @JsonProperty("description") @Nullable String description,
        @JsonProperty("fields") List<Field> fields) {

    public FieldGroup {
        fields = List.copyOf(fields);
    }

    /** {@code FieldGroup.of("totals", Field.required("amount", FieldType.NUMBER))} */
    public static FieldGroup of(String name, Field... fields) {
        return new FieldGroup(name, null, List.of(fields));
    }
}
