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

/**
 * Official Java/Spring Boot SDK for flydocs.
 *
 * <p>flydocs is a pure-multimodal Intelligent Document Processing service:
 * structured field extraction with bounding boxes, validation, authenticity
 * checks, LLM judge, and a business-rule engine.</p>
 *
 * <p>The SDK gives Java callers two ways to talk to the service:</p>
 * <ul>
 *   <li>{@link com.firefly.flydocs.sdk.FlydocsClientAsync} — reactive, returns
 *       {@code Mono<T>} / {@code Flux<T>}. Use this from WebFlux apps or any
 *       caller that already lives on Project Reactor.</li>
 *   <li>{@link com.firefly.flydocs.sdk.FlydocsClient} — blocking facade
 *       wrapping the reactive client. Use this from servlet apps or
 *       command-line tools that don't have an event loop.</li>
 * </ul>
 *
 * <p>Errors come through {@link com.firefly.flydocs.sdk.error.FlydocsException}
 * and its subclasses; webhook signature verification lives in
 * {@link com.firefly.flydocs.sdk.webhook.WebhookVerifier}.</p>
 */
package com.firefly.flydocs.sdk;
