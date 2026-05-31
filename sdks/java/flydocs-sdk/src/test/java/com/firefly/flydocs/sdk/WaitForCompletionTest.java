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

package com.firefly.flydocs.sdk;

import static com.github.tomakehurst.wiremock.client.WireMock.aResponse;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;

import com.firefly.flydocs.sdk.model.ExtractionStatus;
import com.github.tomakehurst.wiremock.WireMockServer;
import java.time.Duration;
import java.util.concurrent.TimeoutException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import reactor.test.StepVerifier;

/**
 * Tests for the {@code waitForCompletion} polling helper on the v1 lifecycle.
 *
 * <p>WireMock scenarios drive the state-machine progression.</p>
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
                  "id": "ext_01",
                  "status": "%s",
                  "submitted_at": "2026-05-17T10:00:00+00:00",
                  "attempts": 0
                }
                """.formatted(status);
    }

    @Test
    void wait_returns_when_state_machine_reaches_succeeded() {
        wm.stubFor(get(urlEqualTo("/api/v1/extractions/ext_01"))
                .inScenario("lifecycle").whenScenarioStateIs("Started")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("queued")))
                .willSetStateTo("running"));
        wm.stubFor(get(urlEqualTo("/api/v1/extractions/ext_01"))
                .inScenario("lifecycle").whenScenarioStateIs("running")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("running")))
                .willSetStateTo("succeeded"));
        wm.stubFor(get(urlEqualTo("/api/v1/extractions/ext_01"))
                .inScenario("lifecycle").whenScenarioStateIs("succeeded")
                .willReturn(aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("succeeded"))));

        StepVerifier.create(client.extractions().waitForCompletion(
                        "ext_01", Duration.ofMillis(10), Duration.ofSeconds(5)))
                .assertNext(s -> assertThat(s.status()).isEqualTo(ExtractionStatus.SUCCEEDED))
                .verifyComplete();
    }

    @Test
    void wait_returns_immediately_on_failed() {
        wm.stubFor(get(urlEqualTo("/api/v1/extractions/ext_01")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("failed"))));
        StepVerifier.create(client.extractions().waitForCompletion(
                        "ext_01", Duration.ofMillis(10), Duration.ofSeconds(5)))
                .assertNext(s -> assertThat(s.status()).isEqualTo(ExtractionStatus.FAILED))
                .verifyComplete();
    }

    @Test
    void wait_times_out_when_extraction_never_finishes() {
        wm.stubFor(get(urlEqualTo("/api/v1/extractions/ext_01")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(statusBody("running"))));
        StepVerifier.create(client.extractions().waitForCompletion(
                        "ext_01", Duration.ofMillis(20), Duration.ofMillis(80)))
                .expectError(TimeoutException.class)
                .verify();
    }

    @Test
    void succeeded_is_terminal_even_when_post_processing_running() {
        // v1 simplification: bbox refinement runs as additive post-processing
        // and does NOT keep the main status non-terminal. Main status of
        // "succeeded" is terminal; bbox-refine completion is observed via
        // post_processing.bbox_refinement.status.
        String body = """
                {
                  "id": "ext_01",
                  "status": "succeeded",
                  "submitted_at": "2026-05-17T10:00:00+00:00",
                  "attempts": 1,
                  "post_processing": {
                    "bbox_refinement": {
                      "status": "running",
                      "attempts": 1
                    }
                  }
                }
                """;
        wm.stubFor(get(urlEqualTo("/api/v1/extractions/ext_01")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody(body)));
        StepVerifier.create(client.extractions().waitForCompletion(
                        "ext_01", Duration.ofMillis(10), Duration.ofSeconds(5)))
                .assertNext(s -> {
                    assertThat(s.status()).isEqualTo(ExtractionStatus.SUCCEEDED);
                    assertThat(s.isTerminal()).isTrue();
                    assertThat(s.postProcessing().bboxRefinement().status().wire()).isEqualTo("running");
                })
                .verifyComplete();
    }
}
