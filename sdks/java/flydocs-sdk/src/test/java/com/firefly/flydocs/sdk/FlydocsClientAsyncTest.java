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
import com.firefly.flydocs.sdk.model.DocumentTypeSpec;
import com.firefly.flydocs.sdk.model.Extraction;
import com.firefly.flydocs.sdk.model.ExtractionListQuery;
import com.firefly.flydocs.sdk.model.ExtractionListResponse;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.ExtractionResult;
import com.firefly.flydocs.sdk.model.ExtractionResultEnvelope;
import com.firefly.flydocs.sdk.model.ExtractionStatus;
import com.firefly.flydocs.sdk.model.Field;
import com.firefly.flydocs.sdk.model.FieldType;
import com.firefly.flydocs.sdk.model.FileInput;
import com.firefly.flydocs.sdk.model.SubmitExtractionRequest;
import com.github.tomakehurst.wiremock.WireMockServer;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import reactor.test.StepVerifier;

/**
 * End-to-end mock tests for the reactive v1 client.
 *
 * <p>Each test stands up a WireMock stub that mimics what the real
 * service would return, calls the SDK, and asserts both halves:
 * the request the SDK put on the wire matches the controller's v1
 * contract (path, body, headers) and the response is decoded onto
 * the typed records.</p>
 */
class FlydocsClientAsyncTest {

    private WireMockServer wm;
    private FlydocsClientAsync client;

    /** Minimal DocumentTypeSpec list reused by tests that don't care about the schema's shape. */
    private static List<DocumentTypeSpec> invoiceSpec() {
        return List.of(
                DocumentTypeSpec.builder("invoice")
                        .addFieldGroup("totals", Field.required("total", FieldType.NUMBER))
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
                                  "version": "26.6.0",
                                  "model":   "anthropic:claude-sonnet-4-6",
                                  "fallback_model": "",
                                  "eda_adapter": "postgres"
                                }
                                """)));

        StepVerifier.create(client.version())
                .assertNext(info -> {
                    assertThat(info.service()).isEqualTo("flydocs");
                    assertThat(info.version()).isEqualTo("26.6.0");
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
                .withRequestBody(matchingJsonPath("$.files[0].filename"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "id": "ext_01",
                                  "status": "success",
                                  "documents": [],
                                  "pipeline": {
                                    "model": "anthropic:claude-sonnet-4-6",
                                    "latency_ms": 4321
                                  }
                                }
                                """)));

        ExtractionRequest req = ExtractionRequest.of(
                List.of(FileInput.ofBytes("hello".getBytes(), "x.pdf", null, null)),
                invoiceSpec());

        StepVerifier.create(client.extract(req, "abc-123", "corr-1"))
                .assertNext(r -> {
                    assertThat(r).isInstanceOf(ExtractionResult.class);
                    assertThat(r.id()).isEqualTo("ext_01");
                    assertThat(r.pipeline().model()).isEqualTo("anthropic:claude-sonnet-4-6");
                    assertThat(r.pipeline().latencyMs()).isEqualTo(4321);
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
                                  "code":   "timeout",
                                  "title":  "Extraction timed out",
                                  "detail": "Pipeline exceeded 60s sync ceiling"
                                }
                                """)));
        ExtractionRequest req = ExtractionRequest.of(
                List.of(FileInput.ofBytes(new byte[]{0}, "x.pdf")),
                invoiceSpec());

        assertThatThrownBy(() -> client.extract(req).block())
                .isInstanceOf(FlydocsHttpException.class)
                .satisfies(t -> {
                    FlydocsHttpException e = (FlydocsHttpException) t;
                    assertThat(e.statusCode()).isEqualTo(408);
                    assertThat(e.code()).isEqualTo("timeout");
                    assertThat(e.detail()).contains("Pipeline exceeded");
                });
    }

    @Test
    void extract_file_too_large_decoded() {
        wm.stubFor(post(urlEqualTo("/api/v1/extract")).willReturn(
                aResponse().withStatus(413)
                        .withHeader("Content-Type", "application/problem+json")
                        .withBody("""
                                {
                                  "code":   "file_too_large",
                                  "title":  "File too large",
                                  "detail": "x.pdf is 50000000 bytes"
                                }
                                """)));
        ExtractionRequest req = ExtractionRequest.of(
                List.of(FileInput.ofBytes(new byte[]{0}, "x.pdf")),
                invoiceSpec());

        assertThatThrownBy(() -> client.extract(req).block())
                .isInstanceOf(FlydocsHttpException.class)
                .satisfies(t -> assertThat(((FlydocsHttpException) t).code()).isEqualTo("file_too_large"));
    }

    // ---------------------------- extractions lifecycle ------------------------

    @Test
    void create_extraction_returns_queued() {
        wm.stubFor(post(urlEqualTo("/api/v1/extractions"))
                .withHeader("Idempotency-Key", equalTo("submit-once"))
                .withRequestBody(containing("\"callback_url\":\"https://example.com/webhook\""))
                .willReturn(aResponse().withStatus(202)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "id": "ext_01",
                                  "status": "queued",
                                  "submitted_at": "2026-05-17T10:00:00+00:00",
                                  "attempts": 0
                                }
                                """)));
        SubmitExtractionRequest req = SubmitExtractionRequest.builder()
                .addFile(FileInput.ofBytes(new byte[]{0}, "x.pdf"))
                .addDocumentType(invoiceSpec().get(0))
                .callbackUrl("https://example.com/webhook")
                .metadata("caller", "test")
                .build();
        StepVerifier.create(client.extractions().create(req, "submit-once", null))
                .assertNext(resp -> {
                    assertThat(resp).isInstanceOf(Extraction.class);
                    assertThat(resp.id()).isEqualTo("ext_01");
                    assertThat(resp.status()).isEqualTo(ExtractionStatus.QUEUED);
                })
                .verifyComplete();
    }

    @Test
    void get_extraction_decodes_status() {
        wm.stubFor(get(urlEqualTo("/api/v1/extractions/ext_01")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "id": "ext_01",
                                  "status": "succeeded",
                                  "submitted_at": "2026-05-17T10:00:00+00:00",
                                  "finished_at":  "2026-05-17T10:01:00+00:00",
                                  "attempts": 1
                                }
                                """)));
        StepVerifier.create(client.extractions().get("ext_01"))
                .assertNext(s -> {
                    assertThat(s).isInstanceOf(Extraction.class);
                    assertThat(s.status()).isEqualTo(ExtractionStatus.SUCCEEDED);
                    assertThat(s.isTerminal()).isTrue();
                })
                .verifyComplete();
    }

    @Test
    void get_result_passes_long_poll_query_params() {
        wm.stubFor(get(urlPathEqualTo("/api/v1/extractions/ext_01/result"))
                .withQueryParam("wait_for_bboxes", equalTo("true"))
                .withQueryParam("timeout", equalTo("10"))
                .willReturn(aResponse().withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "id": "ext_01",
                                  "result": {
                                    "id": "ext_01",
                                    "status": "success",
                                    "documents": [],
                                    "pipeline": {
                                      "model": "anthropic:claude-sonnet-4-6",
                                      "latency_ms": 1500
                                    }
                                  }
                                }
                                """)));
        StepVerifier.create(client.extractions().getResult("ext_01", true, java.time.Duration.ofSeconds(10)))
                .assertNext(r -> {
                    assertThat(r).isInstanceOf(ExtractionResultEnvelope.class);
                    assertThat(r.id()).isEqualTo("ext_01");
                    assertThat(r.result().pipeline().latencyMs()).isEqualTo(1500);
                })
                .verifyComplete();
    }

    @Test
    void list_joins_status_filter_csv() {
        wm.stubFor(get(urlPathEqualTo("/api/v1/extractions"))
                .withQueryParam("status", equalTo("succeeded,failed"))
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
        ExtractionListQuery q = ExtractionListQuery.builder()
                .status(ExtractionStatus.SUCCEEDED)
                .status(ExtractionStatus.FAILED)
                .limit(25)
                .build();
        StepVerifier.create(client.extractions().list(q))
                .assertNext(r -> {
                    assertThat(r).isInstanceOf(ExtractionListResponse.class);
                    assertThat(r.total()).isZero();
                    assertThat(r.limit()).isEqualTo(25);
                })
                .verifyComplete();
    }

    @Test
    void cancel_returns_cancelled() {
        wm.stubFor(delete(urlEqualTo("/api/v1/extractions/ext_01")).willReturn(
                aResponse().withStatus(200).withHeader("Content-Type", "application/json")
                        .withBody("""
                                {
                                  "id": "ext_01",
                                  "status": "cancelled",
                                  "submitted_at": "2026-05-17T10:00:00+00:00",
                                  "attempts": 0
                                }
                                """)));
        StepVerifier.create(client.extractions().cancel("ext_01"))
                .assertNext(s -> assertThat(s.status()).isEqualTo(ExtractionStatus.CANCELLED))
                .verifyComplete();
    }

    @Test
    void cancel_not_cancellable_maps_to_typed_error() {
        wm.stubFor(delete(urlEqualTo("/api/v1/extractions/ext_01")).willReturn(
                aResponse().withStatus(409).withHeader("Content-Type", "application/problem+json")
                        .withBody("""
                                {
                                  "code":   "not_cancellable",
                                  "title":  "Extraction cannot be cancelled",
                                  "detail": "Extraction is running"
                                }
                                """)));
        assertThatThrownBy(() -> client.extractions().cancel("ext_01").block())
                .isInstanceOf(FlydocsHttpException.class)
                .satisfies(t -> {
                    FlydocsHttpException e = (FlydocsHttpException) t;
                    assertThat(e.statusCode()).isEqualTo(409);
                    assertThat(e.code()).isEqualTo("not_cancellable");
                });
    }

    // ---------------------------- request shape proof -------------------

    @Test
    void submit_request_body_uses_snake_case_field_names() {
        wm.stubFor(post(urlEqualTo("/api/v1/extractions")).willReturn(
                aResponse().withStatus(202)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                                {"id":"ext_x","status":"queued","submitted_at":"2026-05-17T10:00:00+00:00","attempts":0}
                                """)));

        SubmitExtractionRequest req = SubmitExtractionRequest.builder()
                .addFile(FileInput.ofBytes(new byte[]{0}, "x.pdf", "application/pdf", null))
                .addDocumentType(invoiceSpec().get(0))
                .callbackUrl("https://example.com/webhook")
                .metadata(Map.of())
                .build();
        client.extractions().create(req).block();

        wm.verify(postRequestedFor(urlEqualTo("/api/v1/extractions"))
                .withRequestBody(matchingJsonPath("$.callback_url"))
                .withRequestBody(matchingJsonPath("$.files[0].content_base64"))
                .withRequestBody(matchingJsonPath("$.files[0].content_type"))
                .withRequestBody(matchingJsonPath("$.document_types[0].id")));
    }
}
