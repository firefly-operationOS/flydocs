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

package com.firefly.flydocs.sdk.model;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Verifies Jackson round-trips the SDK's record DTOs as we expect.
 *
 * <p>Each test pins one half of the wire contract: either the JSON the
 * service emits decodes onto our records, or the records we hand to
 * Jackson serialise back into JSON the service will accept. Skipping
 * this would let drift between camelCase fields (jobId) and snake_case
 * wire keys (job_id) sneak through.</p>
 */
class ModelMappingTest {

    private static ObjectMapper mapper;

    @BeforeAll
    static void mapper() {
        mapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    // ---------------------------- Inputs --------------------------------

    @Test
    void documentInput_from_bytes_base64_encodes() {
        DocumentInput doc = DocumentInput.ofBytes("hello".getBytes(), "x.txt", "text/plain", null);
        assertThat(doc.filename()).isEqualTo("x.txt");
        assertThat(doc.contentBase64()).isEqualTo("aGVsbG8="); // base64("hello")
    }

    @Test
    void documentInput_from_path_reads_disk(@TempDir Path tmp) throws Exception {
        Path file = tmp.resolve("payload.bin");
        Files.write(file, new byte[]{1, 2, 3});
        DocumentInput doc = DocumentInput.ofPath(file);
        assertThat(doc.filename()).isEqualTo("payload.bin");
        assertThat(doc.contentBase64()).isEqualTo("AQID");
    }

    @Test
    void extractionRequest_serialises_with_snake_case() throws Exception {
        ExtractionRequest req = ExtractionRequest.builder()
                .requestId(UUID.fromString("00000000-0000-0000-0000-000000000001"))
                .addDocument(DocumentInput.ofBytes(new byte[]{1, 2}, "x.pdf"))
                .addDocSpec(DocSpec.builder("invoice").addFieldGroup(
                        "totals", FieldSpec.required("total", FieldType.NUMBER)).build())
                .build();
        String json = mapper.writeValueAsString(req);
        assertThat(json).contains("\"request_id\":\"00000000-0000-0000-0000-000000000001\"");
        assertThat(json).contains("\"documents\"");
        assertThat(json).contains("\"docType\"");
    }

    @Test
    void submitJobRequest_serialises_callback_url_as_snake_case() throws Exception {
        SubmitJobRequest req = SubmitJobRequest.builder()
                .addDocument(DocumentInput.ofBytes(new byte[]{0}, "x.pdf"))
                .addDocSpec(DocSpec.builder("invoice").addFieldGroup(
                        "totals", FieldSpec.required("total", FieldType.NUMBER)).build())
                .callbackUrl("https://example.com/webhook")
                .metadata("caller", "test")
                .build();
        String json = mapper.writeValueAsString(req);
        assertThat(json).contains("\"callback_url\":\"https://example.com/webhook\"");
        assertThat(json).contains("\"intention\":\"Extract structured data from the document.\"");
    }

    // ---------------------------- Outputs -------------------------------

    @Test
    void submitJobResponse_decodes() throws Exception {
        String body = """
                {
                  "job_id": "job-1",
                  "status": "QUEUED",
                  "submitted_at": "2026-05-17T10:00:00+00:00"
                }
                """;
        SubmitJobResponse parsed = mapper.readValue(body, SubmitJobResponse.class);
        assertThat(parsed.jobId()).isEqualTo("job-1");
        assertThat(parsed.status()).isEqualTo(JobStatus.QUEUED);
        assertThat(parsed.submittedAt()).isNotNull();
    }

    @Test
    void jobStatusResponse_decodes_full_shape_including_bbox_refine() throws Exception {
        String body = """
                {
                  "job_id": "job-1",
                  "status": "RUNNING",
                  "submitted_at": "2026-05-17T10:00:00+00:00",
                  "started_at":   "2026-05-17T10:00:01+00:00",
                  "attempts": 2,
                  "bbox_refine_status": "pending",
                  "bbox_refine_attempts": 1
                }
                """;
        JobStatusResponse parsed = mapper.readValue(body, JobStatusResponse.class);
        assertThat(parsed.status()).isEqualTo(JobStatus.RUNNING);
        assertThat(parsed.attempts()).isEqualTo(2);
        assertThat(parsed.bboxRefineStatus()).isEqualTo("pending");
        assertThat(parsed.bboxRefineAttempts()).isEqualTo(1);
        assertThat(parsed.isTerminal()).isFalse();
    }

    @Test
    void jobStatusResponse_terminal_helper() throws Exception {
        for (JobStatus terminal : List.of(JobStatus.SUCCEEDED, JobStatus.PARTIAL_SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)) {
            String body = "{\"job_id\":\"j\",\"status\":\"%s\",\"submitted_at\":\"2026-05-17T10:00:00+00:00\"}".formatted(terminal);
            JobStatusResponse parsed = mapper.readValue(body, JobStatusResponse.class);
            assertThat(parsed.isTerminal()).as("status %s is terminal", terminal).isTrue();
        }
    }

    @Test
    void unknown_job_status_decodes_as_unknown_sentinel() {
        // valueOf would throw; fromWire returns UNKNOWN.
        assertThat(JobStatus.fromWire("WEIRD_FUTURE_STATE")).isEqualTo(JobStatus.UNKNOWN);
        assertThat(JobStatus.fromWire(null)).isEqualTo(JobStatus.UNKNOWN);
        assertThat(JobStatus.fromWire("")).isEqualTo(JobStatus.UNKNOWN);
        assertThat(JobStatus.fromWire("SUCCEEDED")).isEqualTo(JobStatus.SUCCEEDED);
    }

    @Test
    void extractionResult_decodes_and_tolerates_unknown_fields() throws Exception {
        String body = """
                {
                  "request_id": "00000000-0000-0000-0000-000000000001",
                  "model": "anthropic:claude-sonnet-4-6",
                  "latency_ms": 4321,
                  "documents": [],
                  "future_field_unknown_to_sdk": {"shiny": true}
                }
                """;
        ExtractionResult parsed = mapper.readValue(body, ExtractionResult.class);
        assertThat(parsed.model()).isEqualTo("anthropic:claude-sonnet-4-6");
        assertThat(parsed.latencyMs()).isEqualTo(4321);
        assertThat(parsed.documents()).isEmpty();
    }

    @Test
    void versionInfo_decodes() throws Exception {
        String body = """
                {
                  "service": "flydocs",
                  "version": "26.5.1",
                  "model":   "anthropic:claude-sonnet-4-6",
                  "fallback_model": "",
                  "eda_adapter": "postgres"
                }
                """;
        VersionInfo info = mapper.readValue(body, VersionInfo.class);
        assertThat(info.service()).isEqualTo("flydocs");
        assertThat(info.edaAdapter()).isEqualTo("postgres");
    }
}
