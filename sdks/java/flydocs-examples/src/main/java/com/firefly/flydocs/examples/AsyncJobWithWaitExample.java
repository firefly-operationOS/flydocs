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
import com.firefly.flydocs.sdk.model.DocumentInput;
import com.firefly.flydocs.sdk.model.JobResult;
import com.firefly.flydocs.sdk.model.JobStatus;
import com.firefly.flydocs.sdk.model.SubmitJobRequest;
import com.firefly.flydocs.sdk.model.SubmitJobResponse;
import java.io.IOException;
import java.nio.file.Path;
import java.time.Duration;

/**
 * 03 — Submit an async job, wait for it, fetch the result.
 *
 * <p>Reactive end-to-end: a single chained {@code Mono} pipeline that
 * submits the job, polls until terminal via {@code waitForCompletion},
 * branches on status, and reads the result. Mirrors
 * {@code sdks/python/examples/03_async_job_with_wait.py}.</p>
 *
 * <pre>{@code
 * mvn -pl flydocs-examples compile exec:java \
 *   -Dexec.mainClass=com.firefly.flydocs.examples.AsyncJobWithWaitExample \
 *   -Dexec.args="path/to/document.pdf"
 * }</pre>
 */
public final class AsyncJobWithWaitExample {

    public static void main(String[] args) throws IOException {
        if (args.length < 1) {
            System.err.println("usage: AsyncJobWithWaitExample <path/to/document.pdf>");
            System.exit(2);
        }
        Path pdf = Path.of(args[0]);

        SubmitJobRequest submitReq = SubmitJobRequest.builder()
                .addDocument(DocumentInput.ofPath(pdf))
                .addDocSpec(ExampleHelpers.invoiceDocSpec())
                .build();

        try (FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
                .baseUrl(ExampleHelpers.defaultBaseUrl())
                .maxAttempts(3)                  // retry transient 5xx on the submit
                .build()) {

            JobResult finalResult = flydocs.submitJob(submitReq)
                    .doOnNext(r -> System.out.printf("queued %s%n", r.jobId()))
                    .map(SubmitJobResponse::jobId)
                    .flatMap(id -> flydocs.waitForCompletion(
                            id, Duration.ofSeconds(2), Duration.ofMinutes(10)))
                    .flatMap(status -> {
                        System.out.printf("terminal status=%s attempts=%d%n",
                                status.status(), status.attempts());
                        if (status.status() != JobStatus.SUCCEEDED
                                && status.status() != JobStatus.PARTIAL_SUCCEEDED) {
                            return reactor.core.publisher.Mono.empty();
                        }
                        return flydocs.getJobResult(status.jobId());
                    })
                    .block();

            if (finalResult == null) {
                System.out.println("no result available (job did not succeed)");
                return;
            }
            System.out.printf("got result with %d documents%n",
                    finalResult.result().documents().size());
        }
    }
}
