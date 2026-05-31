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

/** JSON-Schema-style format hints applied at field validation time. */
public enum StandardFormat {
    DATE("date"),
    DATE_TIME("date-time"),
    TIME("time"),
    EMAIL("email"),
    URI("uri"),
    UUID("uuid"),
    CURRENCY("currency");

    private final String wire;

    StandardFormat(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static StandardFormat fromWire(String value) {
        if (value == null) {
            throw new IllegalArgumentException("StandardFormat value is null");
        }
        for (StandardFormat f : values()) {
            if (f.wire.equals(value)) {
                return f;
            }
        }
        throw new IllegalArgumentException("unknown StandardFormat: " + value);
    }
}
