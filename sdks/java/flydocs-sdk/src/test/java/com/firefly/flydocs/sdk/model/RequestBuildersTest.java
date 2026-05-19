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
 * Builder + typed-record coverage for the request-side type tree.
 *
 * <p>For each builder we (1) construct a non-trivial request, (2)
 * serialise to JSON, and (3) assert the on-wire keys match what the
 * service expects. This pins both halves of the contract — the public
 * Java API and the JSON the service sees.</p>
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
                .escalationThreshold(0.25)
                .escalationModel("anthropic:claude-opus-4-7")
                .build();
        JsonNode json = mapper.valueToTree(opts);
        assertThat(json.get("language_hint").asText()).isEqualTo("es");
        assertThat(json.get("model").asText()).isEqualTo("anthropic:claude-sonnet-4-6");
        assertThat(json.get("stages").get("judge").asBoolean()).isTrue();
        assertThat(json.get("stages").get("bbox_refine").asBoolean()).isTrue();
        assertThat(json.get("escalation_threshold").asDouble()).isEqualTo(0.25);
    }

    @Test
    void field_spec_factories() {
        FieldSpec required = FieldSpec.required("total", FieldType.NUMBER);
        assertThat(required.required()).isTrue();
        assertThat(required.type()).isEqualTo(FieldType.NUMBER);

        FieldSpec optional = FieldSpec.of("currency", FieldType.STRING);
        assertThat(optional.required()).isFalse();
    }

    @Test
    void field_spec_builder_can_attach_validators_and_format() throws Exception {
        FieldSpec spec = FieldSpec.builder("dob")
                .type(FieldType.STRING)
                .required(true)
                .format(StandardFormat.DATE)
                .validator(new StandardValidatorSpec("date"))
                .build();
        JsonNode json = mapper.valueToTree(spec);
        assertThat(json.get("name").asText()).isEqualTo("dob");
        assertThat(json.get("type").asText()).isEqualTo("string");
        assertThat(json.get("required").asBoolean()).isTrue();
        assertThat(json.get("format").asText()).isEqualTo("date");
        assertThat(json.get("standard_validators").get(0).get("type").asText()).isEqualTo("date");
    }

    @Test
    void doc_spec_builder_round_trips() throws Exception {
        DocSpec spec = DocSpec.builder("invoice")
                .description("Vendor invoice")
                .country("ES")
                .addFieldGroup("totals",
                        FieldSpec.required("total_amount", FieldType.NUMBER),
                        FieldSpec.required("currency", FieldType.STRING))
                .build();
        JsonNode json = mapper.valueToTree(spec);
        assertThat(json.get("docType").get("documentType").asText()).isEqualTo("invoice");
        assertThat(json.get("docType").get("description").asText()).isEqualTo("Vendor invoice");
        assertThat(json.get("docType").get("country").asText()).isEqualTo("ES");
        assertThat(json.get("fieldGroups").get(0).get("fieldGroupName").asText()).isEqualTo("totals");
        assertThat(json.get("fieldGroups").get(0).get("fieldGroupFields")).hasSize(2);
    }

    @Test
    void rule_spec_builder_handles_parent_variants() throws Exception {
        RuleSpec rule = RuleSpec.builder("invoice_total_matches", "Total equals sum of line items")
                .addFieldParent("invoice", "total_amount", "line_items")
                .build();
        JsonNode json = mapper.valueToTree(rule);
        assertThat(json.get("id").asText()).isEqualTo("invoice_total_matches");
        JsonNode parent = json.get("parents").get(0);
        // The discriminator must be present on the wire so the service
        // can decode the union variant.
        assertThat(parent.get("parentType").asText()).isEqualTo("field");
        assertThat(parent.get("documentType").asText()).isEqualTo("invoice");
        assertThat(parent.get("fieldNames").get(0).asText()).isEqualTo("total_amount");
    }

    @Test
    void rule_spec_validator_and_rule_ref_parents() throws Exception {
        RuleSpec rule = RuleSpec.builder("composite", "Both checks pass")
                .addValidatorParent("invoice", "signature_present")
                .addRuleParent("upstream_rule")
                .build();
        JsonNode json = mapper.valueToTree(rule);
        assertThat(json.get("parents").get(0).get("parentType").asText()).isEqualTo("validator");
        assertThat(json.get("parents").get(0).get("validatorName").asText()).isEqualTo("signature_present");
        assertThat(json.get("parents").get(1).get("parentType").asText()).isEqualTo("rule");
        assertThat(json.get("parents").get(1).get("ruleId").asText()).isEqualTo("upstream_rule");
    }

    @Test
    void extraction_request_builder_full_round_trip() throws Exception {
        ExtractionRequest req = ExtractionRequest.builder()
                .intention("Extract invoice fields")
                .addDocument(DocumentInput.ofBytes(new byte[]{1, 2, 3}, "x.pdf"))
                .addDocSpec(DocSpec.builder("invoice")
                        .addFieldGroup("totals",
                                FieldSpec.required("total", FieldType.NUMBER))
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
        assertThat(json.get("documents").get(0).get("filename").asText()).isEqualTo("x.pdf");
        assertThat(json.get("docs").get(0).get("docType").get("documentType").asText()).isEqualTo("invoice");
        assertThat(json.get("rules").get(0).get("id").asText()).isEqualTo("r1");
        assertThat(json.get("options").get("stages").get("bbox_refine").asBoolean()).isTrue();
    }

    @Test
    void submit_job_request_builder_full_round_trip() throws Exception {
        SubmitJobRequest req = SubmitJobRequest.builder()
                .addDocument(DocumentInput.ofBytes(new byte[]{0}, "x.pdf"))
                .addDocSpec(DocSpec.builder("invoice")
                        .addFieldGroup("totals",
                                FieldSpec.required("total", FieldType.NUMBER))
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
}
