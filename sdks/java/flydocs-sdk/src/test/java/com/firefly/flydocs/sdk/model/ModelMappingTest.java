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

package com.firefly.flydocs.sdk.model;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Verifies Jackson round-trips the SDK's record DTOs as we expect for the v1 wire.
 *
 * <p>Each test pins one half of the wire contract: either the JSON the
 * service emits decodes onto our records, or the records we hand to
 * Jackson serialise back into JSON the service will accept.</p>
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
    void fileInput_from_bytes_base64_encodes() {
        FileInput doc = FileInput.ofBytes("hello".getBytes(), "x.txt", "text/plain", null);
        assertThat(doc.filename()).isEqualTo("x.txt");
        assertThat(doc.contentBase64()).isEqualTo("aGVsbG8="); // base64("hello")
    }

    @Test
    void fileInput_from_path_reads_disk(@TempDir Path tmp) throws Exception {
        Path file = tmp.resolve("payload.bin");
        Files.write(file, new byte[]{1, 2, 3});
        FileInput doc = FileInput.ofPath(file);
        assertThat(doc.filename()).isEqualTo("payload.bin");
        assertThat(doc.contentBase64()).isEqualTo("AQID");
    }

    @Test
    void extractionRequest_serialises_with_snake_case() throws Exception {
        ExtractionRequest req = ExtractionRequest.builder()
                .addFile(FileInput.ofBytes(new byte[]{1, 2}, "x.pdf"))
                .addDocumentType(DocumentTypeSpec.builder("invoice").addFieldGroup(
                        "totals", Field.required("total", FieldType.NUMBER)).build())
                .build();
        String json = mapper.writeValueAsString(req);
        assertThat(json).contains("\"files\"");
        assertThat(json).contains("\"document_types\"");
        assertThat(json).contains("\"id\":\"invoice\"");
        assertThat(json).contains("\"field_groups\"");
        // Confirm only v1 keys are emitted; nothing camel-cased leaks.
        assertThat(json).doesNotContain("\"docs\"");
        assertThat(json).doesNotContain("docType");
        assertThat(json).doesNotContain("fieldGroup");
    }

    @Test
    void submitExtractionRequest_serialises_callback_url_as_snake_case() throws Exception {
        SubmitExtractionRequest req = SubmitExtractionRequest.builder()
                .addFile(FileInput.ofBytes(new byte[]{0}, "x.pdf"))
                .addDocumentType(DocumentTypeSpec.builder("invoice").addFieldGroup(
                        "totals", Field.required("total", FieldType.NUMBER)).build())
                .callbackUrl("https://example.com/webhook")
                .metadata("caller", "test")
                .build();
        String json = mapper.writeValueAsString(req);
        assertThat(json).contains("\"callback_url\":\"https://example.com/webhook\"");
        assertThat(json).contains("\"intention\":\"Extract structured data from the document.\"");
    }

    // ---------------------------- Outputs -------------------------------

    @Test
    void extraction_decodes_queued() throws Exception {
        String body = """
                {
                  "id": "ext_01",
                  "status": "queued",
                  "submitted_at": "2026-05-17T10:00:00+00:00",
                  "attempts": 0
                }
                """;
        Extraction parsed = mapper.readValue(body, Extraction.class);
        assertThat(parsed.id()).isEqualTo("ext_01");
        assertThat(parsed.status()).isEqualTo(ExtractionStatus.QUEUED);
        assertThat(parsed.submittedAt()).isNotNull();
        assertThat(parsed.isTerminal()).isFalse();
    }

    @Test
    void extraction_decodes_with_post_processing() throws Exception {
        String body = """
                {
                  "id": "ext_01",
                  "status": "succeeded",
                  "submitted_at": "2026-05-17T10:00:00+00:00",
                  "started_at":   "2026-05-17T10:00:01+00:00",
                  "finished_at":  "2026-05-17T10:00:30+00:00",
                  "attempts": 1,
                  "post_processing": {
                    "bbox_refinement": {
                      "status": "running",
                      "started_at": "2026-05-17T10:00:31+00:00",
                      "attempts": 1
                    }
                  }
                }
                """;
        Extraction parsed = mapper.readValue(body, Extraction.class);
        assertThat(parsed.status()).isEqualTo(ExtractionStatus.SUCCEEDED);
        assertThat(parsed.attempts()).isEqualTo(1);
        assertThat(parsed.postProcessing()).isNotNull();
        assertThat(parsed.postProcessing().bboxRefinement()).isNotNull();
        assertThat(parsed.postProcessing().bboxRefinement().status()).isEqualTo(PostProcessingStatus.RUNNING);
        assertThat(parsed.isTerminal()).isTrue();
    }

    @Test
    void extraction_terminal_helper() throws Exception {
        for (ExtractionStatus terminal : List.of(ExtractionStatus.SUCCEEDED, ExtractionStatus.FAILED, ExtractionStatus.CANCELLED)) {
            String body = "{\"id\":\"x\",\"status\":\"%s\",\"submitted_at\":\"2026-05-17T10:00:00+00:00\",\"attempts\":0}".formatted(terminal.wire());
            Extraction parsed = mapper.readValue(body, Extraction.class);
            assertThat(parsed.isTerminal()).as("status %s is terminal", terminal).isTrue();
        }
    }

    @Test
    void unknown_extraction_status_throws() {
        // v1 contract: unknown statuses are not silently swallowed.
        assertThat(catchThrowing(() -> ExtractionStatus.fromWire("WEIRD")))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(ExtractionStatus.fromWire("succeeded")).isEqualTo(ExtractionStatus.SUCCEEDED);
    }

    @Test
    void extractionResult_decodes_and_tolerates_unknown_fields() throws Exception {
        String body = """
                {
                  "id": "ext_01",
                  "status": "success",
                  "documents": [],
                  "pipeline": {
                    "model": "anthropic:claude-sonnet-4-6",
                    "latency_ms": 4321
                  },
                  "future_field_unknown_to_sdk": {"shiny": true}
                }
                """;
        ExtractionResult parsed = mapper.readValue(body, ExtractionResult.class);
        assertThat(parsed.id()).isEqualTo("ext_01");
        assertThat(parsed.status()).isEqualTo("success");
        assertThat(parsed.pipeline().model()).isEqualTo("anthropic:claude-sonnet-4-6");
        assertThat(parsed.pipeline().latencyMs()).isEqualTo(4321);
        assertThat(parsed.documents()).isEmpty();
    }

    @Test
    void versionInfo_decodes() throws Exception {
        String body = """
                {
                  "service": "flydocs",
                  "version": "26.6.0",
                  "model":   "anthropic:claude-sonnet-4-6",
                  "fallback_model": "",
                  "eda_adapter": "postgres"
                }
                """;
        VersionInfo info = mapper.readValue(body, VersionInfo.class);
        assertThat(info.service()).isEqualTo("flydocs");
        assertThat(info.edaAdapter()).isEqualTo("postgres");
    }

    // ---------------------------- Recursive Field ------------------------

    @Test
    void recursive_field_array_of_object_serialises() throws Exception {
        Field lineItems = Field.builder("line_items")
                .type(FieldType.ARRAY)
                .items(Field.builder("row")
                        .type(FieldType.OBJECT)
                        .fields(List.of(
                                Field.of("description", FieldType.STRING),
                                Field.of("quantity", FieldType.NUMBER),
                                Field.of("unit_price", FieldType.NUMBER)))
                        .build())
                .build();
        JsonNode json = mapper.valueToTree(lineItems);
        assertThat(json.get("type").asText()).isEqualTo("array");
        // items is a single recursive Field (not a list).
        assertThat(json.get("items").get("type").asText()).isEqualTo("object");
        assertThat(json.get("items").get("fields")).hasSize(3);
        assertThat(json.get("items").get("fields").get(0).get("name").asText()).isEqualTo("description");
        // Round-trip
        Field back = mapper.treeToValue(json, Field.class);
        assertThat(back.type()).isEqualTo(FieldType.ARRAY);
        assertThat(back.items()).isNotNull();
        assertThat(back.items().type()).isEqualTo(FieldType.OBJECT);
        assertThat(back.items().fields()).hasSize(3);
    }

    // ---------------------------- BBox / Quality / Source enums ---------

    @Test
    void bbox_source_and_quality_use_lowercase_wire() throws Exception {
        BoundingBox b = new BoundingBox(0.1, 0.2, 0.8, 0.3, BboxQuality.GOOD, 0.94, BboxSource.PDF_TEXT, 0.91);
        JsonNode json = mapper.valueToTree(b);
        assertThat(json.get("quality").asText()).isEqualTo("good");
        assertThat(json.get("source").asText()).isEqualTo("pdf_text");
        BoundingBox back = mapper.treeToValue(json, BoundingBox.class);
        assertThat(back.quality()).isEqualTo(BboxQuality.GOOD);
        assertThat(back.source()).isEqualTo(BboxSource.PDF_TEXT);
    }

    @Test
    void judge_status_serialises_lowercase() throws Exception {
        JudgeOutcome o = new JudgeOutcome(JudgeStatus.PASS, 0.99, "evidence", "ok", false);
        JsonNode json = mapper.valueToTree(o);
        assertThat(json.get("status").asText()).isEqualTo("pass");
        assertThat(json.get("flag_for_review").asBoolean()).isFalse();
    }

    private static Throwable catchThrowing(Runnable r) {
        try {
            r.run();
            return null;
        } catch (Throwable t) {
            return t;
        }
    }
}
