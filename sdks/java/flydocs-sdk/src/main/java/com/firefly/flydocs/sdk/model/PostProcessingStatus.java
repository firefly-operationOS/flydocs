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
 * Sub-state for additive post-processing legs (bbox refinement today).
 *
 * <p>An extraction can reach {@link ExtractionStatus#SUCCEEDED} while its
 * {@code post_processing.bbox_refinement.status} is still
 * {@code pending} / {@code running}. The result is readable; the bboxes
 * just haven't been ground against the PDF text layer or OCR words yet.</p>
 */
public enum PostProcessingStatus {
    PENDING("pending"),
    RUNNING("running"),
    SUCCEEDED("succeeded"),
    FAILED("failed");

    private final String wire;

    PostProcessingStatus(String wire) {
        this.wire = wire;
    }

    @JsonValue
    public String wire() {
        return wire;
    }

    @JsonCreator
    public static PostProcessingStatus fromWire(String value) {
        if (value == null) {
            throw new IllegalArgumentException("PostProcessingStatus value is null");
        }
        for (PostProcessingStatus s : values()) {
            if (s.wire.equals(value)) {
                return s;
            }
        }
        throw new IllegalArgumentException("unknown PostProcessingStatus: " + value);
    }

    public boolean isTerminal() {
        return this == SUCCEEDED || this == FAILED;
    }
}
