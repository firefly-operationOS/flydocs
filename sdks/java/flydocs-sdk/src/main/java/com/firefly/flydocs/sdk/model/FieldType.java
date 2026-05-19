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

import com.fasterxml.jackson.annotation.JsonValue;

/**
 * Supported primitives for a {@link FieldSpec}. Mirrors the service-side
 * {@code FieldType} enum; the wire form is the lowercase value
 * ({@code "string"}, {@code "number"}, ...).
 */
public enum FieldType {
    STRING("string"),
    NUMBER("number"),
    INTEGER("integer"),
    BOOLEAN("boolean"),
    ARRAY("array");

    private final String wire;

    FieldType(String wire) {
        this.wire = wire;
    }

    /** JSON wire value. */
    @JsonValue
    public String wire() {
        return wire;
    }
}
