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
import static com.github.tomakehurst.wiremock.client.WireMock.containing;
import static com.github.tomakehurst.wiremock.client.WireMock.delete;
import static com.github.tomakehurst.wiremock.client.WireMock.equalTo;
import static com.github.tomakehurst.wiremock.client.WireMock.get;
import static com.github.tomakehurst.wiremock.client.WireMock.matchingJsonPath;
import static com.github.tomakehurst.wiremock.client.WireMock.post;
import static com.github.tomakehurst.wiremock.client.WireMock.postRequestedFor;
import static com.github.tomakehurst.wiremock.client.WireMock.urlEqualTo;
import static com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo;
import static com.github.tomakehurst.wiremock.core.WireMockConfiguration.options;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.firefly.flydocs.sdk.error.FlydocsHttpException;
import com.firefly.flydocs.sdk.model.DocSpec;
import com.firefly.flydocs.sdk.model.DocumentInput;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.FieldSpec;
import com.firefly.flydocs.sdk.model.FieldType;
import com.firefly.flydocs.sdk.model.JobListResponse;
import com.firefly.flydocs.sdk.model.JobResult;
import com.firefly.flydocs.sdk.model.JobStatus;
import com.firefly.flydocs.sdk.model.JobStatusResponse;
import com.firefly.flydocs.sdk.model.SubmitJobRequest;
import com.firefly.flydocs.sdk.model.SubmitJobResponse;
import com.firefly.flydocs.sdk.model.VersionInfo;
import com.github.tomakehurst.wiremock.WireMockServer;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import reactor.test.StepVerifier;

/**
 * End-to-end mock tests for the reactive client.
 *
 * <p>Each test stands up a WireMock stub that mimics what the real
 * service would return, calls the SDK, and asserts both halves:
 * the request the SDK put on the wire matches the controller's
 * contract (path, body, headers) and the response is decoded onto
 * the typed records.</p>
 */
class FlydocsClientAsyncTest {

    private WireMockServer wm;
    private FlydocsClientAsync client;

    /** Minimal DocSpec list reused by tests that don't care about the schema's shape. */
    private static List<DocSpec> invoiceSpec() {
        return List.of(
                DocSpec.builder("invoice")
                        .addFieldGroup("totals", FieldSpec.required("total", FieldType.NUMBER))
                        .build());
    }

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

    // ---------------------------- identity ------------------------------

    @Test
    void version_decodes() {
        wm.stubFor(get(urlEqualTo("/api/v1/version")).willReturn(
                aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "service": "flydocs",
                                  "version": "26.5.1",
                                  "model":   "anthropic:claude-sonnet-4-6",
                                  "fallback_model": "",
                                  "eda_adapter": "postgres"
                                }
                                """)));

        StepVerifier.create(client.version())
                .assertNext(info -> {
                    assertThat(info.service()).isEqualTo("flydocs");
                    assertThat(info.version()).isEqualTo("26.5.1");
                    assertThat(info.edaAdapter()).isEqualTo("postgres");
                })
                .verifyComplete();
    }

    @Test
    void health_decodes_actuator_map() {
        wm.stubFor(get(urlEqualTo("/actuator/health/readiness")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody("{\"status\":\"UP\",\"components\":{\"db\":\"UP\"}}")));
        StepVerifier.create(client.health())
                .assertNext(m -> assertThat(m).containsEntry("status", "UP"))
                .verifyComplete();
    }

    // ---------------------------- sync extract --------------------------

    @Test
    void extract_decodes_result_and_sends_idempotency_header() {
        wm.stubFor(post(urlEqualTo("/api/v1/extract"))
                .withHeader("Idempotency-Key", equalTo("abc-123"))
                .withHeader("X-Correlation-Id", equalTo("corr-1"))
                .withRequestBody(matchingJsonPath("$.documents[0].filename"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "request_id": "00000000-0000-0000-0000-000000000001",
                                  "model": "anthropic:claude-sonnet-4-6",
                                  "latency_ms": 4321,
                                  "documents": []
                                }
                                """)));

        ExtractionRequest req = ExtractionRequest.of(
                List.of(DocumentInput.ofBytes("hello".getBytes(), "x.pdf", null, null)),
                invoiceSpec());

        StepVerifier.create(client.extract(req, "abc-123", "corr-1"))
                .assertNext(r -> {
                    assertThat(r).isInstanceOf(ExtractionResult.class);
                    assertThat(r.model()).isEqualTo("anthropic:claude-sonnet-4-6");
                    assertThat(r.latencyMs()).isEqualTo(4321);
                })
                .verifyComplete();
    }

    @Test
    void extract_timeout_maps_to_typed_http_exception() {
        wm.stubFor(post(urlEqualTo("/api/v1/extract")).willReturn(
                aResponse().withStatus(408)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "detail": {
                                    "code":   "extraction_timeout",
                                    "title":  "Extraction timed out",
                                    "detail": "Pipeline exceeded 60s sync ceiling"
                                  }
                                }
                                """)));
        ExtractionRequest req = ExtractionRequest.of(
                List.of(DocumentInput.ofBytes(new byte[]{0}, "x.pdf")),
                invoiceSpec());

        assertThatThrownBy(() -> client.extract(req).block())
                .isInstanceOf(FlydocsHttpException.class)
                .satisfies(t -> {
                    FlydocsHttpException e = (FlydocsHttpException) t;
                    assertThat(e.statusCode()).isEqualTo(408);
                    assertThat(e.code()).isEqualTo("extraction_timeout");
                    assertThat(e.detail()).contains("Pipeline exceeded");
                });
    }

    @Test
    void extract_top_level_problem_detail_also_decoded() {
        // Some flydocs error paths put ``code`` at the top level rather
        // than nested under ``detail``.
        wm.stubFor(post(urlEqualTo("/api/v1/extract")).willReturn(
                aResponse().withStatus(413)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "code":   "document_too_large",
                                  "title":  "Document too large",
                                  "detail": "x.pdf is 50000000 bytes"
                                }
                                """)));
        ExtractionRequest req = ExtractionRequest.of(
                List.of(DocumentInput.ofBytes(new byte[]{0}, "x.pdf")),
                invoiceSpec());

        assertThatThrownBy(() -> client.extract(req).block())
                .isInstanceOf(FlydocsHttpException.class)
                .satisfies(t -> assertThat(((FlydocsHttpException) t).code()).isEqualTo("document_too_large"));
    }

    // ---------------------------- jobs lifecycle ------------------------

    @Test
    void submitJob_returns_queued() {
        wm.stubFor(post(urlEqualTo("/api/v1/jobs"))
                .withHeader("Idempotency-Key", equalTo("submit-once"))
                .withRequestBody(containing("\"callback_url\":\"https://example.com/webhook\""))
                .willReturn(aResponse().withStatus(202)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "job_id": "job-1",
                                  "status": "QUEUED",
                                  "submitted_at": "2026-05-17T10:00:00+00:00"
                                }
                                """)));
        SubmitJobRequest req = new SubmitJobRequest(
                null,
                List.of(DocumentInput.ofBytes(new byte[]{0}, "x.pdf")),
                invoiceSpec(),
                null,
                null,
                "https://example.com/webhook",
                Map.of("caller", "test"));
        StepVerifier.create(client.submitJob(req, "submit-once", null))
                .assertNext(resp -> {
                    assertThat(resp).isInstanceOf(SubmitJobResponse.class);
                    assertThat(resp.jobId()).isEqualTo("job-1");
                    assertThat(resp.status()).isEqualTo(JobStatus.QUEUED);
                })
                .verifyComplete();
    }

    @Test
    void getJob_decodes_status() {
        wm.stubFor(get(urlEqualTo("/api/v1/jobs/job-1")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "job_id": "job-1",
                                  "status": "SUCCEEDED",
                                  "submitted_at": "2026-05-17T10:00:00+00:00",
                                  "finished_at":  "2026-05-17T10:01:00+00:00"
                                }
                                """)));
        StepVerifier.create(client.getJob("job-1"))
                .assertNext(s -> {
                    assertThat(s).isInstanceOf(JobStatusResponse.class);
                    assertThat(s.status()).isEqualTo(JobStatus.SUCCEEDED);
                    assertThat(s.isTerminal()).isTrue();
                })
                .verifyComplete();
    }

    @Test
    void getJobResult_passes_long_poll_query_params() {
        wm.stubFor(get(urlPathEqualTo("/api/v1/jobs/job-1/result"))
                .withQueryParam("wait_for_bboxes", equalTo("true"))
                .withQueryParam("timeout", equalTo("10"))
                .willReturn(aResponse().withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "job_id": "job-1",
                                  "result": {
                                    "request_id": "00000000-0000-0000-0000-000000000002",
                                    "model": "anthropic:claude-sonnet-4-6",
                                    "latency_ms": 1500,
                                    "documents": []
                                  }
                                }
                                """)));
        StepVerifier.create(client.getJobResult("job-1", true, java.time.Duration.ofSeconds(10)))
                .assertNext(r -> {
                    assertThat(r).isInstanceOf(JobResult.class);
                    assertThat(r.jobId()).isEqualTo("job-1");
                    assertThat(r.result().latencyMs()).isEqualTo(1500);
                })
                .verifyComplete();
    }

    @Test
    void listJobs_joins_status_filter_csv() {
        wm.stubFor(get(urlPathEqualTo("/api/v1/jobs"))
                .withQueryParam("status", equalTo("SUCCEEDED,PARTIAL_SUCCEEDED"))
                .withQueryParam("limit", equalTo("25"))
                .willReturn(aResponse().withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "items": [],
                                  "total": 0,
                                  "limit": 25,
                                  "offset": 0
                                }
                                """)));
        FlydocsClientAsync.JobListFilter filter = new FlydocsClientAsync.JobListFilter(
                List.of("SUCCEEDED", "PARTIAL_SUCCEEDED"),
                null,
                null,
                null,
                null,
                25,
                0);
        StepVerifier.create(client.listJobs(filter))
                .assertNext(r -> {
                    assertThat(r).isInstanceOf(JobListResponse.class);
                    assertThat(r.total()).isZero();
                    assertThat(r.limit()).isEqualTo(25);
                })
                .verifyComplete();
    }

    @Test
    void cancelJob_returns_cancelled() {
        wm.stubFor(delete(urlEqualTo("/api/v1/jobs/job-1")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "job_id": "job-1",
                                  "status": "CANCELLED",
                                  "submitted_at": "2026-05-17T10:00:00+00:00"
                                }
                                """)));
        StepVerifier.create(client.cancelJob("job-1"))
                .assertNext(s -> assertThat(s.status()).isEqualTo(JobStatus.CANCELLED))
                .verifyComplete();
    }

    @Test
    void cancelJob_not_cancellable_maps_to_typed_error() {
        wm.stubFor(delete(urlEqualTo("/api/v1/jobs/job-1")).willReturn(
                aResponse().withStatus(409).withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "detail": {
                                    "code":   "job_not_cancellable",
                                    "title":  "Job cannot be cancelled",
                                    "detail": "Job is RUNNING"
                                  }
                                }
                                """)));
        assertThatThrownBy(() -> client.cancelJob("job-1").block())
                .isInstanceOf(FlydocsHttpException.class)
                .satisfies(t -> {
                    FlydocsHttpException e = (FlydocsHttpException) t;
                    assertThat(e.statusCode()).isEqualTo(409);
                    assertThat(e.code()).isEqualTo("job_not_cancellable");
                });
    }

    // ---------------------------- request shape proof -------------------

    @Test
    void submit_request_body_uses_snake_case_field_names() {
        wm.stubFor(post(urlEqualTo("/api/v1/jobs")).willReturn(
                aResponse().withStatus(202)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {"job_id":"job-x","status":"QUEUED","submitted_at":"2026-05-17T10:00:00+00:00"}
                                """)));

        SubmitJobRequest req = new SubmitJobRequest(
                null,
                List.of(DocumentInput.ofBytes(new byte[]{0}, "x.pdf", "application/pdf", null)),
                invoiceSpec(),
                null,
                null,
                "https://example.com/webhook",
                Map.of());
        client.submitJob(req).block();

        wm.verify(postRequestedFor(urlEqualTo("/api/v1/jobs"))
                .withRequestBody(matchingJsonPath("$.callback_url"))
                .withRequestBody(matchingJsonPath("$.documents[0].content_base64"))
                .withRequestBody(matchingJsonPath("$.documents[0].content_type")));
    }
}
