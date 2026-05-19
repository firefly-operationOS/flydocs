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
import com.firefly.flydocs.sdk.error.FlydocsHttpException;
import com.firefly.flydocs.sdk.error.FlydocsTimeoutException;
import com.firefly.flydocs.sdk.model.DocumentInput;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import java.io.IOException;
import java.nio.file.Path;
import java.time.Duration;

/**
 * 05 — Typed error handling.
 *
 * <p>The SDK maps every service / transport failure onto a typed
 * exception so callers can branch deterministically. This example
 * deliberately fires a request with a tight timeout against a tiny
 * input -- the goal is to demonstrate the catch shape, not to assert a
 * particular failure. Mirrors
 * {@code sdks/python/examples/05_error_handling.py}.</p>
 *
 * <pre>{@code
 * mvn -pl flydocs-examples compile exec:java \
 *   -Dexec.mainClass=com.firefly.flydocs.examples.ErrorHandlingExample \
 *   -Dexec.args="path/to/invoice.pdf"
 * }</pre>
 */
public final class ErrorHandlingExample {

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("usage: ErrorHandlingExample <path/to/invoice.pdf>");
            System.exit(2);
        }
        Path pdf = Path.of(args[0]);

        ExtractionRequest req = ExtractionRequest.builder()
                .addDocument(DocumentInput.ofPath(pdf))
                .addDocSpec(ExampleHelpers.invoiceDocSpec())
                .build();

        try (FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
                .baseUrl(ExampleHelpers.defaultBaseUrl())
                // Intentionally tight so a real extraction probably trips it.
                .timeout(Duration.ofSeconds(2))
                .build()) {

            try {
                flydocs.extract(req).block();
                System.out.println("extraction succeeded -- unexpected with a 2s budget!");
            } catch (FlydocsTimeoutException e) {
                System.out.println("hit the SDK's timeout -- " + e.getMessage());
            } catch (FlydocsHttpException e) {
                System.out.printf("HTTP %d %s -- %s%n",
                        e.statusCode(),
                        e.code() == null || e.code().isEmpty() ? "(no code)" : e.code(),
                        e.detail() == null || e.detail().isEmpty() ? e.title() : e.detail());
                // Example branch: fall through to async on synchronous timeout.
                if ("extraction_timeout".equals(e.code())) {
                    System.out.println("would fall through to submitJob() in a real app");
                }
            } catch (RuntimeException e) {
                System.out.println("transport / unexpected error: " + e);
            }
        }
    }
}
