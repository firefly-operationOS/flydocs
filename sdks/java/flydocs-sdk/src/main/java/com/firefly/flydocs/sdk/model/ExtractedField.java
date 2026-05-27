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
 * One extracted field. Recursive for arrays and objects.
 *
 * <p>{@code value} is one of: {@code String} / {@code Long} / {@code Double}
 * / {@code Boolean} / {@code List<ExtractedField>} (for arrays + objects)
 * / {@code null}. The recursion is unbounded.</p>
 *
 * <p>Wire keys: {@code value} / {@code pages} / {@code validation}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExtractedField(
        @JsonProperty("name") String name,
        @JsonProperty("value") @Nullable Object value,
        @JsonProperty("pages") List<Integer> pages,
        @JsonProperty("confidence") double confidence,
        @JsonProperty("bbox") @Nullable BoundingBox bbox,
        @JsonProperty("validation") FieldValidation validation,
        @JsonProperty("judge") JudgeOutcome judge,
        @JsonProperty("notes") @Nullable String notes) {

    public ExtractedField {
        pages = pages == null ? List.of() : List.copyOf(pages);
    }
}
