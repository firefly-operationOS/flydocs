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
import com.firefly.flydocs.sdk.model.DocumentTypeSpec;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.Field;
import com.firefly.flydocs.sdk.model.FieldType;
import com.firefly.flydocs.sdk.model.FileInput;
import java.io.IOException;
import java.nio.file.Path;

/**
 * 01 — Hello, flydocs.
 *
 * <p>The smallest runnable example: build a one-field schema, send a PDF,
 * print the extracted result. Mirrors
 * {@code sdks/python/examples/01_first_extraction.py}.</p>
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

        DocumentTypeSpec invoice = DocumentTypeSpec.builder("invoice")
                .description("Any invoice with at least a total amount")
                .addFieldGroup(
                        "totals",
                        Field.required("total_amount", FieldType.NUMBER),
                        Field.required("currency", FieldType.STRING))
                .build();

        ExtractionRequest req = ExtractionRequest.builder()
                .addFile(FileInput.ofPath(pdf))
                .addDocumentType(invoice)
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
            System.out.printf("id=%s  status=%s  model=%s  latency=%dms  documents=%d%n",
                    result.id(),
                    result.status(),
                    result.pipeline().model(),
                    result.pipeline().latencyMs(),
                    result.documents().size());
            for (var doc : result.documents()) {
                System.out.printf("  doc[type=%s] pages=%s field_groups=%d%n",
                        doc.type(), doc.pages(), doc.fieldGroups().size());
            }
        }
    }
}
