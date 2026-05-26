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

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.util.Map;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Builder + typed-record coverage for the v1 request-side type tree.
 *
 * <p>Each builder is exercised through (1) construction, (2) JSON
 * serialisation, (3) assertions on the on-wire keys.</p>
 */
class RequestBuildersTest {

    private static ObjectMapper mapper;

    @BeforeAll
    static void setUp() {
        mapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    @Test
    void stageToggles_defaults_match_service_defaults() {
        StageToggles s = StageToggles.defaults();
        assertThat(s.splitter()).isFalse();
        assertThat(s.classifier()).isTrue();
        assertThat(s.fieldValidation()).isTrue();
        assertThat(s.judge()).isFalse();
        assertThat(s.bboxRefine()).isFalse();
    }

    @Test
    void stage_toggles_builder_is_fluent() {
        StageToggles s = StageToggles.builder()
                .judge(true)
                .bboxRefine(true)
                .transform(true)
                .build();
        assertThat(s.judge()).isTrue();
        assertThat(s.bboxRefine()).isTrue();
        assertThat(s.transform()).isTrue();
        // Unset fields fall back to the service defaults.
        assertThat(s.classifier()).isTrue();
    }

    @Test
    void extraction_options_builder_serialises_correctly() throws Exception {
        ExtractionOptions opts = ExtractionOptions.builder()
                .languageHint("es")
                .model("anthropic:claude-sonnet-4-6")
                .stages(StageToggles.builder().judge(true).bboxRefine(true).build())
                .escalation(0.25, "anthropic:claude-opus-4-7")
                .build();
        JsonNode json = mapper.valueToTree(opts);
        assertThat(json.get("language_hint").asText()).isEqualTo("es");
        assertThat(json.get("model").asText()).isEqualTo("anthropic:claude-sonnet-4-6");
        assertThat(json.get("stages").get("judge").asBoolean()).isTrue();
        assertThat(json.get("stages").get("bbox_refine").asBoolean()).isTrue();
        // v1 collapses escalation into a sub-object.
        assertThat(json.get("escalation").get("threshold").asDouble()).isEqualTo(0.25);
        assertThat(json.get("escalation").get("model").asText()).isEqualTo("anthropic:claude-opus-4-7");
        assertThat(json.has("escalation_threshold")).isFalse();
        assertThat(json.has("escalation_model")).isFalse();
    }

    @Test
    void field_factories() {
        Field required = Field.required("total", FieldType.NUMBER);
        assertThat(required.required()).isTrue();
        assertThat(required.type()).isEqualTo(FieldType.NUMBER);

        Field optional = Field.of("currency", FieldType.STRING);
        assertThat(optional.required()).isNull();
    }

    @Test
    void field_builder_can_attach_validators_and_format() throws Exception {
        Field f = Field.builder("dob")
                .type(FieldType.STRING)
                .required(true)
                .format(StandardFormat.DATE)
                .validator(new ValidatorSpec("date"))
                .build();
        JsonNode json = mapper.valueToTree(f);
        assertThat(json.get("name").asText()).isEqualTo("dob");
        assertThat(json.get("type").asText()).isEqualTo("string");
        assertThat(json.get("required").asBoolean()).isTrue();
        assertThat(json.get("format").asText()).isEqualTo("date");
        // v1: validators[].name (not standard_validators[].type)
        assertThat(json.get("validators").get(0).get("name").asText()).isEqualTo("date");
    }

    @Test
    void validator_spec_with_params_serialises() throws Exception {
        ValidatorSpec v = new ValidatorSpec("vat_id", Map.of("country", "ES"));
        JsonNode json = mapper.valueToTree(v);
        assertThat(json.get("name").asText()).isEqualTo("vat_id");
        assertThat(json.get("params").get("country").asText()).isEqualTo("ES");
        assertThat(json.get("severity").asText()).isEqualTo("error");
    }

    @Test
    void document_type_spec_builder_round_trips() throws Exception {
        DocumentTypeSpec spec = DocumentTypeSpec.builder("invoice")
                .description("Vendor invoice")
                .country("ES")
                .addFieldGroup("totals",
                        Field.required("total_amount", FieldType.NUMBER),
                        Field.required("currency", FieldType.STRING))
                .build();
        JsonNode json = mapper.valueToTree(spec);
        // v1: id at top level (no docType.documentType stutter).
        assertThat(json.get("id").asText()).isEqualTo("invoice");
        assertThat(json.get("description").asText()).isEqualTo("Vendor invoice");
        assertThat(json.get("country").asText()).isEqualTo("ES");
        // v1: snake_case field_groups[].name / .fields
        assertThat(json.get("field_groups").get(0).get("name").asText()).isEqualTo("totals");
        assertThat(json.get("field_groups").get(0).get("fields")).hasSize(2);
        // No v0 keys.
        assertThat(json.has("docType")).isFalse();
        assertThat(json.has("fieldGroups")).isFalse();
    }

    @Test
    void rule_spec_builder_handles_field_parent() throws Exception {
        RuleSpec rule = RuleSpec.builder("invoice_total_matches", "Total equals sum of line items")
                .addFieldParent("invoice", "total_amount", "line_items")
                .build();
        JsonNode json = mapper.valueToTree(rule);
        assertThat(json.get("id").asText()).isEqualTo("invoice_total_matches");
        JsonNode parent = json.get("parents").get(0);
        // v1 discriminator is "kind", not "parentType".
        assertThat(parent.get("kind").asText()).isEqualTo("field");
        assertThat(parent.get("document_type").asText()).isEqualTo("invoice");
        // v1: "fields" (not "fieldNames")
        assertThat(parent.get("fields").get(0).asText()).isEqualTo("total_amount");
    }

    @Test
    void rule_spec_validator_and_rule_parents() throws Exception {
        RuleSpec rule = RuleSpec.builder("composite", "Both checks pass")
                .addValidatorParent("invoice", "signature_present")
                .addRuleParent("upstream_rule")
                .build();
        JsonNode json = mapper.valueToTree(rule);
        assertThat(json.get("parents").get(0).get("kind").asText()).isEqualTo("validator");
        // v1: "validator" (not "validatorName")
        assertThat(json.get("parents").get(0).get("validator").asText()).isEqualTo("signature_present");
        assertThat(json.get("parents").get(1).get("kind").asText()).isEqualTo("rule");
        // v1: "rule" (not "ruleId")
        assertThat(json.get("parents").get(1).get("rule").asText()).isEqualTo("upstream_rule");
    }

    @Test
    void rule_parent_round_trips_through_jackson() throws Exception {
        RuleParent original = new RuleParent.Field("invoice", java.util.List.of("a", "b"));
        String json = mapper.writeValueAsString(original);
        RuleParent decoded = mapper.readValue(json, RuleParent.class);
        assertThat(decoded).isInstanceOf(RuleParent.Field.class);
        assertThat(((RuleParent.Field) decoded).fields()).containsExactly("a", "b");
    }

    @Test
    void extraction_request_builder_full_round_trip() throws Exception {
        ExtractionRequest req = ExtractionRequest.builder()
                .intention("Extract invoice fields")
                .addFile(FileInput.ofBytes(new byte[]{1, 2, 3}, "x.pdf"))
                .addDocumentType(DocumentTypeSpec.builder("invoice")
                        .addFieldGroup("totals",
                                Field.required("total", FieldType.NUMBER))
                        .build())
                .addRule(RuleSpec.builder("r1", "Total > 0")
                        .addFieldParent("invoice", "total")
                        .build())
                .options(ExtractionOptions.builder()
                        .stages(StageToggles.builder().bboxRefine(true).judge(true).build())
                        .build())
                .build();
        JsonNode json = mapper.valueToTree(req);
        assertThat(json.get("intention").asText()).isEqualTo("Extract invoice fields");
        assertThat(json.get("files").get(0).get("filename").asText()).isEqualTo("x.pdf");
        // v1: document_types (not docs)
        assertThat(json.get("document_types").get(0).get("id").asText()).isEqualTo("invoice");
        assertThat(json.get("rules").get(0).get("id").asText()).isEqualTo("r1");
        assertThat(json.get("options").get("stages").get("bbox_refine").asBoolean()).isTrue();
    }

    @Test
    void submit_extraction_request_builder_full_round_trip() throws Exception {
        SubmitExtractionRequest req = SubmitExtractionRequest.builder()
                .addFile(FileInput.ofBytes(new byte[]{0}, "x.pdf"))
                .addDocumentType(DocumentTypeSpec.builder("invoice")
                        .addFieldGroup("totals",
                                Field.required("total", FieldType.NUMBER))
                        .build())
                .callbackUrl("https://example.com/webhook")
                .metadata("caller", "test")
                .metadata(Map.of("environment", "staging"))
                .build();
        JsonNode json = mapper.valueToTree(req);
        assertThat(json.get("callback_url").asText()).isEqualTo("https://example.com/webhook");
        assertThat(json.get("metadata").get("caller").asText()).isEqualTo("test");
        assertThat(json.get("metadata").get("environment").asText()).isEqualTo("staging");
    }

    @Test
    void file_input_uses_expected_type_not_document_type() throws Exception {
        FileInput f = new FileInput("x.pdf", "AA==", "application/pdf", "invoice");
        JsonNode json = mapper.valueToTree(f);
        assertThat(json.get("expected_type").asText()).isEqualTo("invoice");
        assertThat(json.has("document_type")).isFalse();
    }
}
