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

package com.firefly.flydocs.examples;

import com.firefly.flydocs.sdk.model.DocumentTypeSpec;
import com.firefly.flydocs.sdk.model.Field;
import com.firefly.flydocs.sdk.model.FieldType;
import com.firefly.flydocs.sdk.model.RuleSpec;

/**
 * Shared test fixtures for the worked examples. Mirrors the intent of
 * {@code sdks/python/examples/examples_helpers.py}.
 *
 * <p>Defines a representative invoice schema + a couple of business
 * rules so each example can focus on one SDK feature instead of
 * rebuilding the schema.</p>
 */
final class ExampleHelpers {

    private ExampleHelpers() {}

    /** Invoice schema with a totals group and a customer group. */
    static DocumentTypeSpec invoiceDocumentType() {
        return DocumentTypeSpec.builder("invoice")
                .description("Standard invoice with totals + customer block")
                .addFieldGroup(
                        "totals",
                        Field.required("total_amount", FieldType.NUMBER),
                        Field.required("currency", FieldType.STRING))
                .addFieldGroup(
                        "customer",
                        Field.required("customer_name", FieldType.STRING),
                        Field.of("customer_email", FieldType.STRING))
                .build();
    }

    /** Rule: total must be positive. */
    static RuleSpec totalIsPositiveRule() {
        return RuleSpec.builder("total_is_positive", "total_amount > 0")
                .addFieldParent("invoice", "total_amount")
                .build();
    }

    /** Rule: customer name must be non-empty. */
    static RuleSpec customerNamePresentRule() {
        return RuleSpec.builder("customer_name_present", "customer_name != ''")
                .addFieldParent("invoice", "customer_name")
                .build();
    }

    /** Default base URL when {@code FLYDOCS_BASE_URL} is unset. */
    static String defaultBaseUrl() {
        String env = System.getenv("FLYDOCS_BASE_URL");
        return env != null && !env.isEmpty() ? env : "http://localhost:8400";
    }
}
