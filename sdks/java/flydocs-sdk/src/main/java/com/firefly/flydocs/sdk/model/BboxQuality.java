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

/**
 * Coarse-grained verdict on whether a {@link BoundingBox} is trustworthy.
 *
 * <p>v1 dropped the {@code empty} placeholder — absence of a bbox is
 * signalled by a {@code null} {@link BoundingBox} on the consuming field,
 * not by a synthetic zero-area box.</p>
 */
public enum BboxQuality {
    GOOD("good"),
    POOR("poor"),
    SUSPICIOUS("suspicious"),
    INVALID("invalid");

    private final String wire;

    BboxQuality(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static BboxQuality fromWire(String value) {
        if (value == null) {
            throw new IllegalArgumentException("BboxQuality value is null");
        }
        for (BboxQuality q : values()) {
            if (q.wire.equals(value)) {
                return q;
            }
        }
        throw new IllegalArgumentException("unknown BboxQuality: " + value);
    }
}
