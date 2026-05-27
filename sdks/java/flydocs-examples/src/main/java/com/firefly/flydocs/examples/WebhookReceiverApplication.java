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

package com.firefly.flydocs.examples;

import com.firefly.flydocs.sdk.model.EventEnvelope;
import com.firefly.flydocs.sdk.spring.FlydocsWebhook;
import com.firefly.flydocs.sdk.webhook.WebhookVerificationException;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 04 — Webhook receiver, Spring Boot edition.
 *
 * <p>Spin up an app that listens for flydocs extraction webhooks. The
 * {@link FlydocsWebhook @FlydocsWebhook} parameter is verified upstream
 * by the starter and deserialised into an {@link EventEnvelope} record
 * — the controller method never sees the raw bytes or the signature
 * header. Mirrors
 * {@code sdks/python/examples/04_webhook_receiver_fastapi.py}.</p>
 *
 * <pre>{@code
 * flydocs:
 *   base-url: http://localhost:8400        # required to enable the starter
 *   webhook:
 *     secret: ${FLYDOCS_WEBHOOK_SECRET}
 * }</pre>
 *
 * <pre>{@code
 * FLYDOCS_BASE_URL=http://localhost:8400 \
 * FLYDOCS_WEBHOOK_SECRET=super-secret \
 * mvn -pl flydocs-examples spring-boot:run \
 *     -Dspring-boot.run.mainClass=com.firefly.flydocs.examples.WebhookReceiverApplication
 * }</pre>
 *
 * <p>Then ``POST`` the signed payload to
 * {@code http://localhost:8080/flydocs/webhook} with
 * {@code X-Flydocs-Signature: sha256=<hex>}.</p>
 */
@SpringBootApplication
@RestController
public class WebhookReceiverApplication {

    public static void main(String[] args) {
        SpringApplication.run(WebhookReceiverApplication.class, args);
    }

    @PostMapping(value = "/flydocs/webhook", consumes = "application/json")
    public ResponseEntity<String> onWebhook(@FlydocsWebhook EventEnvelope event) {
        System.out.printf("verified webhook: event_type=%s id=%s status=%s%n",
                event.eventType(),
                event.extraction().id(),
                event.extraction().status());
        if (EventEnvelope.TYPE_EXTRACTION_COMPLETED.equals(event.eventType())
                && event.result() != null) {
            System.out.printf("  documents=%d  rule_results=%d%n",
                    event.result().documents().size(),
                    event.result().ruleResults().size());
        }
        return ResponseEntity.accepted().body("ok");
    }

    @ExceptionHandler(WebhookVerificationException.class)
    public ResponseEntity<String> onBadSignature(WebhookVerificationException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(e.getMessage());
    }
}
