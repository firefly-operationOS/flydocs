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

/** Validation outcome attached to one {@link ExtractedField}. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record FieldValidation(
        @JsonProperty("valid") boolean valid,
        @JsonProperty("errors") List<FieldValidationError> errors) {

    public FieldValidation {
        errors = errors == null ? List.of() : List.copyOf(errors);
    }

    /** Sentinel for the "no errors" case. */
    public static FieldValidation ok() {
        return new FieldValidation(true, List.of());
    }
}
