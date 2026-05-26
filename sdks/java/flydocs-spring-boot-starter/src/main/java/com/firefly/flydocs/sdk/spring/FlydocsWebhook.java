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

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Annotation that marks a controller method parameter as an inbound flydocs
 * webhook envelope. The Spring Boot starter resolves it by:
 *
 * <ol>
 *   <li>Reading the raw request body bytes.</li>
 *   <li>Verifying the {@code X-Flydocs-Signature} HMAC-SHA256 header against
 *       the configured {@code flydocs.webhook.hmac-secret}.</li>
 *   <li>Deserialising the body onto an
 *       {@link com.firefly.flydocs.sdk.model.EventEnvelope} record.</li>
 * </ol>
 *
 * <p>Signature mismatches surface as a
 * {@link com.firefly.flydocs.sdk.webhook.WebhookVerificationException}.</p>
 *
 * <pre>{@code
 * @PostMapping("/flydocs/webhook")
 * public ResponseEntity<Void> handle(@FlydocsWebhook EventEnvelope event) {
 *     if (event.eventType().equals(EventEnvelope.TYPE_EXTRACTION_COMPLETED)) {
 *         // … handle completion
 *     }
 *     return ResponseEntity.accepted().build();
 * }
 * }</pre>
 */
@Target(ElementType.PARAMETER)
@Retention(RetentionPolicy.RUNTIME)
public @interface FlydocsWebhook {
}
