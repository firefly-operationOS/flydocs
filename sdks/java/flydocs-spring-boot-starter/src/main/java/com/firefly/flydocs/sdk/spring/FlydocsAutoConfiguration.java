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

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnClass;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * Auto-configures the flydocs SDK in any Spring Boot 3.x application.
 *
 * <p>Bean wiring:</p>
 * <ul>
 *   <li>{@link FlydocsClientAsync} — reactive client, conditional on
 *       {@code flydocs.base-url} being set.</li>
 *   <li>{@link FlydocsClient} — blocking facade over the async client.</li>
 *   <li>{@link WebhookVerifier} — only when {@code flydocs.webhook.secret} is set.</li>
 *   <li>{@link FlydocsWebhookArgumentResolver} + {@link FlydocsWebhookWebMvcConfigurer}
 *       — only when the {@link WebhookVerifier} bean is present and Spring MVC is
 *       on the classpath.</li>
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
        if (props.getApiKey() != null && !props.getApiKey().isEmpty()) {
            b.apiKey(props.getApiKey());
        }
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
    @ConditionalOnProperty(prefix = "flydocs.webhook", name = "secret")
    public WebhookVerifier flydocsWebhookVerifier(FlydocsProperties props) {
        String secret = props.getWebhook().getSecret();
        if (secret == null || secret.isEmpty()) {
            throw new IllegalStateException("flydocs.webhook.secret must be set");
        }
        return new WebhookVerifier(secret);
    }

    /**
     * Default Jackson mapper for webhook deserialisation. Falls back to a
     * private instance when the application context has no
     * {@link ObjectMapper} primary bean.
     */
    @Bean(name = "flydocsWebhookObjectMapper")
    @ConditionalOnMissingBean(name = "flydocsWebhookObjectMapper")
    public ObjectMapper flydocsWebhookObjectMapper() {
        return new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    /** Resolver that powers {@code @FlydocsWebhook} controller parameters. */
    @Bean
    @ConditionalOnBean(WebhookVerifier.class)
    @ConditionalOnClass(name = "jakarta.servlet.http.HttpServletRequest")
    @ConditionalOnMissingBean
    public FlydocsWebhookArgumentResolver flydocsWebhookArgumentResolver(
            WebhookVerifier verifier, ObjectMapper flydocsWebhookObjectMapper) {
        return new FlydocsWebhookArgumentResolver(verifier, flydocsWebhookObjectMapper);
    }

    /** Wires the argument resolver into Spring MVC's resolver chain. */
    @Bean
    @ConditionalOnBean(FlydocsWebhookArgumentResolver.class)
    @ConditionalOnClass(WebMvcConfigurer.class)
    public FlydocsWebhookWebMvcConfigurer flydocsWebhookWebMvcConfigurer(
            FlydocsWebhookArgumentResolver resolver) {
        return new FlydocsWebhookWebMvcConfigurer(resolver);
    }
}
