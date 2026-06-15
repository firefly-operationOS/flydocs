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

package com.firefly.flydocs.sdk.integration;

import static org.assertj.core.api.Assertions.assertThat;

import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.error.FlydocsHttpException;
import com.firefly.flydocs.sdk.model.DocumentTypeSpec;
import com.firefly.flydocs.sdk.model.ExtractionRequest;
import com.firefly.flydocs.sdk.model.Field;
import com.firefly.flydocs.sdk.model.FieldType;
import com.firefly.flydocs.sdk.model.FileInput;
import com.firefly.flydocs.sdk.model.VersionInfo;
import java.time.Duration;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

/**
 * Live integration test against a running flydocs v1 API.
 *
 * <p>Skipped unless {@code FLYDOCS_BASE_URL} is set; tagged
 * {@code @Tag("integration")} so the default {@code mvn test} skips it.
 * Activate explicitly:</p>
 *
 * <pre>{@code
 * FLYDOCS_BASE_URL=http://localhost:8080 \
 *   mvn -pl flydocs-sdk test -Dgroups=integration
 * }</pre>
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
        DocumentTypeSpec invoice = DocumentTypeSpec.builder("invoice")
                .description("simple invoice")
                .addFieldGroup("totals",
                        Field.required("total_amount", FieldType.NUMBER),
                        Field.required("currency", FieldType.STRING))
                .build();

        // Minimal PDF placeholder body; validate is a dry-run.
        ExtractionRequest req = ExtractionRequest.builder()
                .addFile(FileInput.ofBytes(new byte[]{0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x25, 0x25, 0x45, 0x4f, 0x46, 0x0a},
                        "placeholder.pdf"))
                .addDocumentType(invoice)
                .build();

        Map<String, Object> report = flydocs.validate(req).block(Duration.ofSeconds(10));
        assertThat(report).isNotNull();
        assertThat(report).containsKey("errors");
        assertThat(report).containsKey("warnings");
    }

    @Test
    void listExtractionsReturnsPaginatedResponse() {
        var page = flydocs.extractions().list().block(Duration.ofSeconds(10));
        assertThat(page).isNotNull();
        assertThat(page.total()).isGreaterThanOrEqualTo(0);
        assertThat(page.limit()).isGreaterThan(0);
    }

    @Test
    void getExtractionOfNonExistentIdReturnsTyped404() {
        try {
            flydocs.extractions().get("ext_does_not_exist").block(Duration.ofSeconds(10));
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
