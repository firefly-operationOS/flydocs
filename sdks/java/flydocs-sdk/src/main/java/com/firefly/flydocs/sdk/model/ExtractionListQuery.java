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

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import org.jspecify.annotations.Nullable;

/**
 * Query parameters for {@code GET /api/v1/extractions}.
 *
 * <p>Use the {@link #builder()} for fluent construction.</p>
 */
public record ExtractionListQuery(
        List<ExtractionStatus> statuses,
        List<PostProcessingStatus> postProcessingStatuses,
        @Nullable String idempotencyKey,
        @Nullable OffsetDateTime createdAfter,
        @Nullable OffsetDateTime createdBefore,
        int limit,
        int offset) {

    public ExtractionListQuery {
        statuses = statuses == null ? List.of() : List.copyOf(statuses);
        postProcessingStatuses = postProcessingStatuses == null ? List.of() : List.copyOf(postProcessingStatuses);
        if (limit <= 0) {
            limit = 50;
        }
        if (offset < 0) {
            offset = 0;
        }
    }

    public static ExtractionListQuery defaults() {
        return new ExtractionListQuery(List.of(), List.of(), null, null, null, 50, 0);
    }

    public static Builder builder() {
        return new Builder();
    }

    /** Fluent builder. */
    public static final class Builder {
        private final List<ExtractionStatus> statuses = new ArrayList<>();
        private final List<PostProcessingStatus> postProcessingStatuses = new ArrayList<>();
        private @Nullable String idempotencyKey;
        private @Nullable OffsetDateTime createdAfter;
        private @Nullable OffsetDateTime createdBefore;
        private int limit = 50;
        private int offset = 0;

        public Builder status(ExtractionStatus s) { this.statuses.add(s); return this; }
        public Builder statuses(List<ExtractionStatus> v) {
            this.statuses.clear();
            this.statuses.addAll(v);
            return this;
        }
        public Builder postProcessingStatus(PostProcessingStatus s) {
            this.postProcessingStatuses.add(s);
            return this;
        }
        public Builder postProcessingStatuses(List<PostProcessingStatus> v) {
            this.postProcessingStatuses.clear();
            this.postProcessingStatuses.addAll(v);
            return this;
        }
        public Builder idempotencyKey(@Nullable String v) { this.idempotencyKey = v; return this; }
        public Builder createdAfter(@Nullable OffsetDateTime v) { this.createdAfter = v; return this; }
        public Builder createdBefore(@Nullable OffsetDateTime v) { this.createdBefore = v; return this; }
        public Builder limit(int v) { this.limit = v; return this; }
        public Builder offset(int v) { this.offset = v; return this; }

        public ExtractionListQuery build() {
            return new ExtractionListQuery(
                    List.copyOf(statuses),
                    List.copyOf(postProcessingStatuses),
                    idempotencyKey,
                    createdAfter,
                    createdBefore,
                    limit,
                    offset);
        }
    }
}
