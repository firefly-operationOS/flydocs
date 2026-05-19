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

package com.firefly.flydocs.sdk.spring;

import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnClass;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;

/**
 * Auto-configures the flydocs SDK in any Spring Boot 3.5.x application.
 *
 * <p>Bean wiring:</p>
 *
 * <ul>
 *   <li>{@link FlydocsClientAsync} — reactive client, conditional on
 *       {@code flydocs.base-url} being set and on no other
 *       {@code FlydocsClientAsync} bean already existing.</li>
 *   <li>{@link FlydocsClient} — blocking facade over the async client,
 *       same conditions plus the async bean.</li>
 *   <li>{@link WebhookVerifier} — only when
 *       {@code flydocs.webhook.hmac-secret} is set.</li>
 * </ul>
 *
 * <p>Both client beans declare {@code destroyMethod="close"}, so the
 * Netty pool is released cleanly when the Spring context shuts down.</p>
 */
@AutoConfiguration
@ConditionalOnClass(FlydocsClientAsync.class)
@ConditionalOnProperty(prefix = "flydocs", name = "base-url")
@EnableConfigurationProperties(FlydocsProperties.class)
public class FlydocsAutoConfiguration {

    @Bean(destroyMethod = "close")
    @ConditionalOnMissingBean
    public FlydocsClientAsync flydocsClientAsync(FlydocsProperties props) {
        FlydocsClientAsync.Builder b = FlydocsClientAsync.builder()
                .baseUrl(props.getBaseUrl())
                .timeout(props.getTimeout())
                .maxAttempts(props.getMaxAttempts())
                .retryMinBackoff(props.getRetryMinBackoff())
                .maxConnections(props.getMaxConnections())
                .pendingAcquireTimeout(props.getPendingAcquireTimeout())
                .maxInMemorySize(props.getMaxInMemorySize());
        if (props.getTenantId() != null && !props.getTenantId().isEmpty()) {
            b.defaultHeader("X-Tenant-Id", props.getTenantId());
        }
        return b.build();
    }

    @Bean(destroyMethod = "close")
    @ConditionalOnMissingBean
    public FlydocsClient flydocsClient(FlydocsClientAsync async) {
        return new FlydocsClient(async);
    }

    @Bean
    @ConditionalOnMissingBean
    @ConditionalOnProperty(prefix = "flydocs.webhook", name = "hmac-secret")
    public WebhookVerifier flydocsWebhookVerifier(FlydocsProperties props) {
        String secret = props.getWebhook().getHmacSecret();
        if (secret == null || secret.isEmpty()) {
            // ConditionalOnProperty already filtered this, but the
            // accessor is nullable -- be explicit so the constructor
            // contract is clear.
            throw new IllegalStateException("flydocs.webhook.hmac-secret must be set");
        }
        return new WebhookVerifier(secret);
    }
}
