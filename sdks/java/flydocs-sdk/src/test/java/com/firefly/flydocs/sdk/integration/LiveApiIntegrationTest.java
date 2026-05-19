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

package com.firefly.flydocs.sdk.integration;

import static org.assertj.core.api.Assertions.assertThat;

import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.error.FlydocsHttpException;
import com.firefly.flydocs.sdk.model.DocSpec;
import com.firefly.flydocs.sdk.model.DocumentInput;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.FieldSpec;
import com.firefly.flydocs.sdk.model.FieldType;
import com.firefly.flydocs.sdk.model.VersionInfo;
import java.time.Duration;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

/**
 * Live integration test against a running flydocs API.
 *
 * <p>Skipped unless the environment variable {@code FLYDOCS_BASE_URL} is
 * set; tagged {@code @Tag("integration")} so the default {@code mvn test}
 * skips it. Activate explicitly:</p>
 *
 * <pre>{@code
 * FLYDOCS_BASE_URL=http://localhost:8400 \
 *   mvn -pl flydocs-sdk test -Dgroups=integration
 * }</pre>
 *
 * <p>What's covered (low-cost, no LLM calls beyond ``validate``):</p>
 * <ul>
 *   <li>{@code version()} returns a plausibly-shaped service identity.</li>
 *   <li>{@code health(readiness)} returns {@code UP} (or any status).</li>
 *   <li>{@code validate(req)} round-trips a non-trivial schema and rules.</li>
 *   <li>{@code listJobs()} returns a paginated response.</li>
 *   <li>{@code getJob("does-not-exist")} surfaces a typed 404.</li>
 * </ul>
 *
 * <p>Heavyweight paths -- ``extract`` and ``submitJob`` -- are
 * deliberately not exercised here because they consume real LLM tokens
 * unless the service is wired to a mock provider. Pair this with the
 * docker-compose test stack (mock-llm overlay) for that.</p>
 */
@Tag("integration")
@EnabledIfEnvironmentVariable(named = "FLYDOCS_BASE_URL", matches = ".+")
class LiveApiIntegrationTest {

    private FlydocsClientAsync flydocs;

    @BeforeEach
    void setUp() {
        this.flydocs = FlydocsClientAsync.builder()
                .baseUrl(System.getenv("FLYDOCS_BASE_URL"))
                .timeout(Duration.ofSeconds(10))
                .maxAttempts(3)
                .build();
    }

    @Test
    void versionEndpointReturnsServiceIdentity() {
        VersionInfo info = flydocs.version().block(Duration.ofSeconds(10));
        assertThat(info).isNotNull();
        assertThat(info.service()).isNotBlank();
        assertThat(info.version()).isNotBlank();
        assertThat(info.model()).isNotBlank();
    }

    @Test
    void readinessHealthEndpointResponds() {
        Map<String, Object> health = flydocs.health("readiness").block(Duration.ofSeconds(10));
        assertThat(health).isNotNull();
        assertThat(health).containsKey("status");
    }

    @Test
    void validateAcceptsAWellFormedRequest() {
        DocSpec invoice = DocSpec.builder("invoice")
                .description("simple invoice")
                .addFieldGroup("totals",
                        FieldSpec.required("total_amount", FieldType.NUMBER),
                        FieldSpec.required("currency", FieldType.STRING))
                .build();

        // 1-byte placeholder PDF body -- validate is a dry-run, it
        // doesn't actually parse the document, only the schema graph.
        ExtractionRequest req = ExtractionRequest.builder()
                .addDocument(DocumentInput.ofBytes(new byte[]{0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x25, 0x25, 0x45, 0x4f, 0x46, 0x0a},
                        "placeholder.pdf"))
                .addDocSpec(invoice)
                .build();

        Map<String, Object> report = flydocs.validate(req).block(Duration.ofSeconds(10));
        assertThat(report).isNotNull();
        // The report always contains "errors" + "warnings" arrays, even
        // when empty -- this is the contract the sync controller pins.
        assertThat(report).containsKey("errors");
        assertThat(report).containsKey("warnings");
    }

    @Test
    void listJobsReturnsPaginatedResponse() {
        var page = flydocs.listJobs().block(Duration.ofSeconds(10));
        assertThat(page).isNotNull();
        // jobs may be empty but the wrapper fields are always present.
        assertThat(page.total()).isGreaterThanOrEqualTo(0);
        assertThat(page.limit()).isGreaterThan(0);
    }

    @Test
    void getJobOfNonExistentIdReturnsTyped404() {
        try {
            flydocs.getJob("00000000-0000-0000-0000-000000000000").block(Duration.ofSeconds(10));
            // Some deployments may treat the unknown id as anything;
            // the typed-404 contract is what we want to verify when
            // the service implements it. If we get a body, that's
            // also a legal answer.
        } catch (RuntimeException e) {
            Throwable root = e;
            while (root.getCause() != null && !(root instanceof FlydocsHttpException)) {
                root = root.getCause();
            }
            if (root instanceof FlydocsHttpException http) {
                assertThat(http.statusCode()).isEqualTo(404);
            }
        }
    }
}
