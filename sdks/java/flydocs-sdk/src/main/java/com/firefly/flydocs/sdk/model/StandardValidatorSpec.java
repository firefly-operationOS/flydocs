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
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * One built-in validator declaration attached to a {@link FieldSpec}.
 *
 * <p>{@code type} is a free string so new server-side validators don't
 * require an SDK release; the canonical names live in
 * {@code flydocs.interfaces.enums.standard_validator}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_DEFAULT)
public record StandardValidatorSpec(
        String type,
        @Nullable Map<String, Object> params,
        Severity severity) {

    /** Default severity is {@link Severity#ERROR} -- mirrors the service's contract. */
    public StandardValidatorSpec {
        if (severity == null) {
            severity = Severity.ERROR;
        }
    }

    public StandardValidatorSpec(String type) {
        this(type, null, Severity.ERROR);
    }

    public StandardValidatorSpec(String type, Map<String, Object> params) {
        this(type, params, Severity.ERROR);
    }

    /** Whether a validation error is hard (``error``) or soft (``warning``). */
    public enum Severity {
        ERROR("error"),
        WARNING("warning");

        private final String wire;

        Severity(String wire) {
            this.wire = wire;
        }

        @com.fasterxml.jackson.annotation.JsonValue
        public String wire() {
            return wire;
        }
    }
}
