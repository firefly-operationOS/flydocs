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

/** Judge verdict on a single extracted field. Wire values are lowercase. */
public enum JudgeStatus {
    PASS("pass"),
    FAIL("fail"),
    UNCERTAIN("uncertain");

    private final String wire;

    JudgeStatus(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static JudgeStatus fromWire(String value) {
        if (value == null) {
            throw new IllegalArgumentException("JudgeStatus value is null");
        }
        for (JudgeStatus s : values()) {
            if (s.wire.equals(value)) {
                return s;
            }
        }
        throw new IllegalArgumentException("unknown JudgeStatus: " + value);
    }
}
