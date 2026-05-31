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
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Supported primitives for a {@link Field}.
 *
 * <p>{@code array} requires the field to set {@code items} (a single
 * recursive {@link Field} describing the row shape). {@code object}
 * requires {@code fields} (a non-empty list of member {@link Field}s).
 * Primitives forbid both. Mirrors the service-side {@code FieldType} enum;
 * wire form is lowercase ({@code "string"}, {@code "number"}, …).</p>
 */
public enum FieldType {
    STRING("string"),
    NUMBER("number"),
    INTEGER("integer"),
    BOOLEAN("boolean"),
    ARRAY("array"),
    OBJECT("object");

    private final String wire;

    FieldType(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static FieldType fromWire(String value) {
        if (value == null) {
            throw new IllegalArgumentException("FieldType value is null");
        }
        for (FieldType t : values()) {
            if (t.wire.equals(value)) {
                return t;
            }
        }
        throw new IllegalArgumentException("unknown FieldType: " + value);
    }
}
