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

package com.firefly.flydocs.sdk.spring;

import static org.assertj.core.api.Assertions.assertThat;

import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

class FlydocsAutoConfigurationTest {

    private final ApplicationContextRunner runner = new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(FlydocsAutoConfiguration.class));

    @Test
    void doesNotRegisterClientsWithoutBaseUrl() {
        runner.run(ctx -> {
            assertThat(ctx).doesNotHaveBean(FlydocsClientAsync.class);
            assertThat(ctx).doesNotHaveBean(FlydocsClient.class);
            assertThat(ctx).doesNotHaveBean(WebhookVerifier.class);
        });
    }

    @Test
    void registersAsyncAndBlockingClientsWhenBaseUrlSet() {
        runner
                .withPropertyValues("flydocs.base-url=http://localhost:8400")
                .run(ctx -> {
                    assertThat(ctx).hasSingleBean(FlydocsClientAsync.class);
                    assertThat(ctx).hasSingleBean(FlydocsClient.class);
                    // Webhook verifier stays absent without a secret.
                    assertThat(ctx).doesNotHaveBean(WebhookVerifier.class);
                });
    }

    @Test
    void registersWebhookVerifierWhenSecretSet() {
        runner
                .withPropertyValues(
                        "flydocs.base-url=http://localhost:8400",
                        "flydocs.webhook.secret=super-secret")
                .run(ctx -> {
                    assertThat(ctx).hasSingleBean(WebhookVerifier.class);
                    assertThat(ctx).hasSingleBean(FlydocsWebhookArgumentResolver.class);
                });
    }

    @Test
    void honoursTimeoutAndRetryProperties() {
        runner
                .withPropertyValues(
                        "flydocs.base-url=http://localhost:8400",
                        "flydocs.api-key=my-key",
                        "flydocs.timeout=30s",
                        "flydocs.max-attempts=3",
                        "flydocs.retry-min-backoff=500ms",
                        "flydocs.max-connections=20",
                        "flydocs.tenant-id=test-tenant")
                .run(ctx -> {
                    assertThat(ctx).hasSingleBean(FlydocsClientAsync.class);
                    FlydocsProperties props = ctx.getBean(FlydocsProperties.class);
                    assertThat(props.getApiKey()).isEqualTo("my-key");
                    assertThat(props.getMaxAttempts()).isEqualTo(3);
                    assertThat(props.getRetryMinBackoff().toMillis()).isEqualTo(500);
                    assertThat(props.getMaxConnections()).isEqualTo(20);
                    assertThat(props.getTenantId()).isEqualTo("test-tenant");
                });
    }

    @Test
    void userBeanOverridesAutoConfiguration() {
        runner
                .withPropertyValues("flydocs.base-url=http://localhost:8400")
                .withUserConfiguration(CustomConfig.class)
                .run(ctx -> {
                    assertThat(ctx).hasSingleBean(FlydocsClientAsync.class);
                    // Our user bean wins over the auto-config.
                    assertThat(ctx.getBean(FlydocsClientAsync.class))
                            .isSameAs(ctx.getBean("customAsync"));
                });
    }

    @Configuration
    static class CustomConfig {
        @Bean(destroyMethod = "close")
        FlydocsClientAsync customAsync() {
            return FlydocsClientAsync.builder().baseUrl("http://override:9999").build();
        }
    }
}
