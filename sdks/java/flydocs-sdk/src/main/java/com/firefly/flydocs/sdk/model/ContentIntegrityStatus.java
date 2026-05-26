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

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

/** Overall content-integrity verdict for a {@link Document}. Lowercase wire values. */
public enum ContentIntegrityStatus {
    VALID("valid"),
    INVALID("invalid"),
    UNCERTAIN("uncertain");

    private final String wire;

    ContentIntegrityStatus(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static ContentIntegrityStatus fromWire(String value) {
        if (value == null) {
            throw new IllegalArgumentException("ContentIntegrityStatus value is null");
        }
        for (ContentIntegrityStatus s : values()) {
            if (s.wire.equals(value)) {
                return s;
            }
        }
        throw new IllegalArgumentException("unknown ContentIntegrityStatus: " + value);
    }
}
