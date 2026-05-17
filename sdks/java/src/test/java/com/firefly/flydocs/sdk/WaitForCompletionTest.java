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

package com.firefly.flydocs.sdk;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;

import com.firefly.flydocs.sdk.model.JobStatus;
import com.github.tomakehurst.wiremock.WireMockServer;
import java.time.Duration;
import java.util.concurrent.TimeoutException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import reactor.test.StepVerifier;

/**
 * Tests for the job-polling helper.
 *
 * <p>WireMock's {@code .willReturn} can chain responses via scenarios;
 * we use {@code inScenario} so each successive poll returns a
 * different state and the helper sees the state machine progress.</p>
 */
class WaitForCompletionTest {

    private WireMockServer wm;
    private FlydocsClientAsync client;

    @BeforeEach
    void setUp() {
        wm = new WireMockServer(options().dynamicPort());
        wm.start();
        client = FlydocsClientAsync.builder().baseUrl("http://localhost:" + wm.port()).build();
    }

    @AfterEach
    void tearDown() {
        wm.stop();
    }

    private static String statusBody(String status) {
        return """
                {
                  "job_id": "job-1",
                  "status": "%s",
                  "submitted_at": "2026-05-17T10:00:00+00:00"
                }
                """.formatted(status);
    }

    @Test
    void wait_returns_when_state_machine_reaches_succeeded() {
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1"))
                .inScenario("lifecycle").whenScenarioStateIs("Started")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("QUEUED")))
                .willSetStateTo("running"));
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1"))
                .inScenario("lifecycle").whenScenarioStateIs("running")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("RUNNING")))
                .willSetStateTo("succeeded"));
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1"))
                .inScenario("lifecycle").whenScenarioStateIs("succeeded")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("SUCCEEDED"))));

        StepVerifier.create(client.waitForCompletion(
                        "job-1", Duration.ofMillis(10), Duration.ofSeconds(5)))
                .assertNext(s -> assertThat(s.status()).isEqualTo(JobStatus.SUCCEEDED))
                .verifyComplete();
    }

    @Test
    void wait_returns_immediately_on_failed() {
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("FAILED"))));
        StepVerifier.create(client.waitForCompletion(
                        "job-1", Duration.ofMillis(10), Duration.ofSeconds(5)))
                .assertNext(s -> assertThat(s.status()).isEqualTo(JobStatus.FAILED))
                .verifyComplete();
    }

    @Test
    void wait_times_out_when_job_never_finishes() {
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("RUNNING"))));
        StepVerifier.create(client.waitForCompletion(
                        "job-1", Duration.ofMillis(20), Duration.ofMillis(80)))
                .expectError(TimeoutException.class)
                .verify();
    }

    @Test
    void refining_bboxes_is_not_terminal() {
        // REFINING_BBOXES is an intermediate state — the helper keeps
        // polling until the refiner finishes (status -> SUCCEEDED).
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1"))
                .inScenario("refine").whenScenarioStateIs("Started")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("REFINING_BBOXES")))
                .willSetStateTo("done"));
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1"))
                .inScenario("refine").whenScenarioStateIs("done")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("SUCCEEDED"))));

        StepVerifier.create(client.waitForCompletion(
                        "job-1", Duration.ofMillis(10), Duration.ofSeconds(5)))
                .assertNext(s -> assertThat(s.status()).isEqualTo(JobStatus.SUCCEEDED))
                .verifyComplete();
    }
}
