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
 * Lifecycle state of an async extraction.
 *
 * <p>One linear state machine in v1: {@code queued -> running -> succeeded |
 * failed | cancelled}. Post-processing (bbox refinement today, more later)
 * lives on the separate {@link PostProcessingStatus} machine on
 * {@link Extraction#postProcessing()} and never gates the main status.</p>
 *
 * <p>Wire form is lowercase ({@code "queued"}, {@code "running"}, …) to
 * match the v1 contract.</p>
 */
public enum ExtractionStatus {
    QUEUED("queued"),
    RUNNING("running"),
    SUCCEEDED("succeeded"),
    FAILED("failed"),
    CANCELLED("cancelled");

    private final String wire;

    ExtractionStatus(String wire) {
        this.wire = wire;
    }

    /** JSON wire value (lowercase). */
    @JsonValue
    public String wire() {
        return wire;
    }

    /**
     * Parse the wire-form string into an {@link ExtractionStatus}. Throws
     * {@link IllegalArgumentException} for unknown values.
     */
    @JsonCreator
    public static ExtractionStatus fromWire(String value) {
        if (value == null) {
            throw new IllegalArgumentException("ExtractionStatus value is null");
        }
        for (ExtractionStatus s : values()) {
            if (s.wire.equals(value)) {
                return s;
            }
        }
        throw new IllegalArgumentException("unknown ExtractionStatus: " + value);
    }

    /** True when no further main-status transition is expected. */
    public boolean isTerminal() {
        return this == SUCCEEDED || this == FAILED || this == CANCELLED;
    }

    /**
     * True when an extraction in this status carries a readable
     * {@code ExtractionResult}. Only {@code succeeded} does.
     */
    public boolean hasResult() {
        return this == SUCCEEDED;
    }
}
