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

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * One named built-in validator attached to a {@link Field}.
 *
 * <p>The dispatch key is {@code name} (not {@code type} — renamed to avoid
 * collision with {@link Field#type()} when both appear in the same parent
 * envelope). Canonical catalogue names: {@code iban}, {@code bic},
 * {@code phone_e164}, {@code vat_id}, {@code email}, {@code uri}, {@code uuid},
 * {@code date}, {@code datetime}, {@code time}, {@code iso_8601}, …</p>
 *
 * <p>{@link Severity#ERROR} (default) hard-fails the field. {@link Severity#WARNING}
 * records the issue but keeps the field {@code valid=true}.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ValidatorSpec(
        @JsonProperty("name") String name,
        @JsonProperty("params") @Nullable Map<String, Object> params,
        @JsonProperty("severity") Severity severity) {

    public ValidatorSpec {
        if (severity == null) {
            severity = Severity.ERROR;
        }
    }

    public ValidatorSpec(String name) {
        this(name, null, Severity.ERROR);
    }

    public ValidatorSpec(String name, Map<String, Object> params) {
        this(name, params, Severity.ERROR);
    }

    /** Hard error vs soft warning. */
    public enum Severity {
        ERROR("error"),
        WARNING("warning");

        private final String wire;

        Severity(String wire) {
            this.wire = wire;
        }

        @JsonValue
        public String wire() {
            return wire;
        }

        @JsonCreator
        public static Severity fromWire(String value) {
            if (value == null) {
                throw new IllegalArgumentException("Severity value is null");
            }
            for (Severity s : values()) {
                if (s.wire.equals(value)) {
                    return s;
                }
            }
            throw new IllegalArgumentException("unknown Severity: " + value);
        }
    }
}
