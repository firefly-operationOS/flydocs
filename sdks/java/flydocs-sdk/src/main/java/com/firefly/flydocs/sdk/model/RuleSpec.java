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

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.ArrayList;
import java.util.List;

/** One business rule expressed as a natural-language predicate over its parents. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record RuleSpec(
        String id,
        String predicate,
        List<RuleParent> parents,
        RuleOutputSpec output) {

    public RuleSpec {
        parents = parents == null ? List.of() : List.copyOf(parents);
        if (output == null) output = RuleOutputSpec.bool();
    }

    public static Builder builder(String id, String predicate) {
        return new Builder(id, predicate);
    }

    /** Fluent builder for a single rule. */
    public static final class Builder {
        private final String id;
        private final String predicate;
        private final List<RuleParent> parents = new ArrayList<>();
        private RuleOutputSpec output = RuleOutputSpec.bool();

        Builder(String id, String predicate) {
            this.id = id;
            this.predicate = predicate;
        }

        public Builder addParent(RuleParent p) { this.parents.add(p); return this; }
        public Builder addFieldParent(String documentType, String... fieldNames) {
            return addParent(new RuleParent.FieldParent(documentType, List.of(fieldNames)));
        }
        public Builder addValidatorParent(String documentType, String validatorName) {
            return addParent(new RuleParent.ValidatorParent(documentType, validatorName));
        }
        public Builder addRuleParent(String ruleId) {
            return addParent(new RuleParent.RuleRef(ruleId));
        }
        public Builder output(RuleOutputSpec o) { this.output = o; return this; }

        public RuleSpec build() {
            return new RuleSpec(id, predicate, List.copyOf(parents), output);
        }
    }
}
