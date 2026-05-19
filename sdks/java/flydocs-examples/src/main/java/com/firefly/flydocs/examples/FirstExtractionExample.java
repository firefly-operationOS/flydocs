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

import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.model.DocSpec;
import com.firefly.flydocs.sdk.model.DocumentInput;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.FieldSpec;
import com.firefly.flydocs.sdk.model.FieldType;
import java.io.IOException;
import java.nio.file.Path;

/**
 * 01 — Hello, flydocs.
 *
 * <p>The smallest runnable example: build a one-field schema, send a
 * PDF, print the extracted result. Mirrors
 * {@code sdks/python/examples/01_first_extraction.py}.</p>
 *
 * <p>Run with a flydocs service reachable at
 * {@code FLYDOCS_BASE_URL} (default {@code http://localhost:8400}):</p>
 *
 * <pre>{@code
 * mvn -pl flydocs-examples compile exec:java \
 *   -Dexec.mainClass=com.firefly.flydocs.examples.FirstExtractionExample \
 *   -Dexec.args="path/to/invoice.pdf"
 * }</pre>
 */
public final class FirstExtractionExample {

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("usage: FirstExtractionExample <path/to/document.pdf>");
            System.exit(2);
        }
        Path pdf = Path.of(args[0]);

        DocSpec invoice = DocSpec.builder("invoice")
                .description("Any invoice with at least a total amount")
                .addFieldGroup(
                        "totals",
                        FieldSpec.required("total_amount", FieldType.NUMBER),
                        FieldSpec.required("currency", FieldType.STRING))
                .build();

        ExtractionRequest req = ExtractionRequest.builder()
                .addDocument(DocumentInput.ofPath(pdf))
                .addDocSpec(invoice)
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
            System.out.printf("model=%s  latency=%dms  documents=%d%n",
                    result.model(), result.latencyMs(), result.documents().size());
            // The deeply-nested per-document shape is intentionally a
            // raw map (the SDK doesn't hard-code field schemas) -- pull
            // attributes by key.
            for (var doc : result.documents()) {
                System.out.printf(
                        "  doc[type=%s]%n",
                        doc.getOrDefault("document_type", "?"));
            }
        }
    }
}
