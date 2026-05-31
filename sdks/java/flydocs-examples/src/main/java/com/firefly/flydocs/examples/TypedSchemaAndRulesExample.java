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

package com.firefly.flydocs.examples;

import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.model.ExtractionOptions;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.FileInput;
import com.firefly.flydocs.sdk.model.StageToggles;
import java.io.IOException;
import java.nio.file.Path;

/**
 * 02 — Typed schema + business rules.
 *
 * <p>Uses {@link ExampleHelpers} for the schema + a couple of rules, and
 * turns on the {@code judge} and {@code rule_engine} stages explicitly
 * so the result carries both extracted values AND rule evaluations.
 * Mirrors {@code sdks/python/examples/02_typed_schema_and_rules.py}.</p>
 *
 * <pre>{@code
 * mvn -pl flydocs-examples compile exec:java \
 *   -Dexec.mainClass=com.firefly.flydocs.examples.TypedSchemaAndRulesExample \
 *   -Dexec.args="path/to/invoice.pdf"
 * }</pre>
 */
public final class TypedSchemaAndRulesExample {

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("usage: TypedSchemaAndRulesExample <path/to/invoice.pdf>");
            System.exit(2);
        }
        Path pdf = Path.of(args[0]);

        ExtractionRequest req = ExtractionRequest.builder()
                .addFile(FileInput.ofPath(pdf))
                .addDocumentType(ExampleHelpers.invoiceDocumentType())
                .addRule(ExampleHelpers.totalIsPositiveRule())
                .addRule(ExampleHelpers.customerNamePresentRule())
                .options(ExtractionOptions.builder()
                        .stages(StageToggles.builder().judge(true).ruleEngine(true).build())
                        .build())
                .build();

        try (FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
                .baseUrl(ExampleHelpers.defaultBaseUrl())
                .build()) {

            ExtractionResult result = flydocs.extract(req).block();
            if (result == null) {
                System.err.println("no result returned");
                System.exit(1);
                return;
            }
            System.out.printf("documents=%d  rule_results=%d%n",
                    result.documents().size(),
                    result.ruleResults().size());
            for (var rr : result.ruleResults()) {
                System.out.printf("  rule %s -> %s%n",
                        rr.ruleId(), rr.output());
            }
        }
    }
}
