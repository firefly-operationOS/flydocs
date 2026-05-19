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

import com.firefly.flydocs.sdk.webhook.WebhookVerificationException;
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * 04 — Webhook receiver, reactive Spring Boot edition.
 *
 * <p>Spin up an app that listens for flydocs job-completion webhooks,
 * verifies the HMAC, and prints the parsed payload. Mirrors the intent
 * of {@code sdks/python/examples/04_webhook_receiver_fastapi.py} but
 * uses Spring's annotated WebFlux controller for parity with the
 * starter's autoconfigure surface.</p>
 *
 * <p>Required configuration (via {@code application.yaml} or env):</p>
 *
 * <pre>{@code
 * flydocs:
 *   base-url: http://localhost:8400         # not strictly needed for the receiver,
 *                                           # but required to enable the starter
 *   webhook:
 *     hmac-secret: ${FLYDOCS_WEBHOOK_HMAC_SECRET}
 * }</pre>
 *
 * <p>Run with:</p>
 *
 * <pre>{@code
 * FLYDOCS_BASE_URL=http://localhost:8400 \
 * FLYDOCS_WEBHOOK_HMAC_SECRET=super-secret \
 * mvn -pl flydocs-examples spring-boot:run \
 *     -Dspring-boot.run.mainClass=com.firefly.flydocs.examples.WebhookReceiverApplication
 * }</pre>
 *
 * <p>Then ``POST`` the job webhook to {@code http://localhost:8080/flydocs/webhook}
 * with the body the service signed and the {@code X-Flydocs-Signature}
 * header set to {@code sha256=<hex>}.</p>
 */
@SpringBootApplication
@RestController
public class WebhookReceiverApplication {

    private final WebhookVerifier verifier;

    public WebhookReceiverApplication(WebhookVerifier verifier) {
        this.verifier = verifier;
    }

    public static void main(String[] args) {
        SpringApplication.run(WebhookReceiverApplication.class, args);
    }

    @PostMapping(value = "/flydocs/webhook", consumes = "application/json")
    public ResponseEntity<String> onWebhook(
            @RequestHeader(value = "X-Flydocs-Signature", required = false) String signature,
            @RequestBody byte[] body) {
        try {
            verifier.verify(body, signature);
        } catch (WebhookVerificationException e) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).body(e.getMessage());
        }
        // In a real app: parse the JSON, dispatch to a domain handler,
        // update DB, etc. Here we just print the size so the example
        // stays small.
        System.out.printf("verified webhook: %d bytes%n", body.length);
        return ResponseEntity.accepted().body("ok");
    }
}
