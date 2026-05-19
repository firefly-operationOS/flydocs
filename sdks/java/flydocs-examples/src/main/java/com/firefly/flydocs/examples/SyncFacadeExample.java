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

import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.model.DocumentInput;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.VersionInfo;
import java.io.IOException;
import java.nio.file.Path;

/**
 * 06 — Blocking facade for non-reactive callers.
 *
 * <p>If you're in a servlet stack, a CLI, or anywhere else where an
 * event loop is inconvenient, use {@link FlydocsClient} -- it wraps
 * the reactive client and {@code .block()}s on every call. Mirrors
 * {@code sdks/python/examples/06_sync_facade.py}.</p>
 *
 * <pre>{@code
 * mvn -pl flydocs-examples compile exec:java \
 *   -Dexec.mainClass=com.firefly.flydocs.examples.SyncFacadeExample \
 *   -Dexec.args="path/to/invoice.pdf"
 * }</pre>
 */
public final class SyncFacadeExample {

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("usage: SyncFacadeExample <path/to/invoice.pdf>");
            System.exit(2);
        }
        Path pdf = Path.of(args[0]);

        try (FlydocsClient flydocs = FlydocsClient.builder()
                .baseUrl(ExampleHelpers.defaultBaseUrl())
                .build()) {

            VersionInfo info = flydocs.version();
            System.out.printf("service: %s %s%n", info.service(), info.version());

            ExtractionRequest req = ExtractionRequest.builder()
                    .addDocument(DocumentInput.ofPath(pdf))
                    .addDocSpec(ExampleHelpers.invoiceDocSpec())
                    .build();

            ExtractionResult result = flydocs.extract(req);
            System.out.printf("model=%s  latency=%dms  documents=%d%n",
                    result.model(), result.latencyMs(), result.documents().size());
        }
    }
}
