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

package com.firefly.flydocs.sdk.model;

import com.fasterxml.jackson.annotation.JsonSubTypes;
import com.fasterxml.jackson.annotation.JsonTypeInfo;

/**
 * Sealed union of post-extraction transformation directives.
 *
 * <p>Discriminator is {@code type}. New declarative kinds add a new
 * permitted record; the engine dispatches on the wire value.</p>
 *
 * <ul>
 *   <li>{@link EntityResolutionTransformation} — deterministic dedup of
 *       array-rows by token overlap.</li>
 *   <li>{@link LlmTransformation} — free-form LLM call against a target
 *       group.</li>
 * </ul>
 */
@JsonTypeInfo(
        use = JsonTypeInfo.Id.NAME,
        include = JsonTypeInfo.As.PROPERTY,
        property = "type")
@JsonSubTypes({
        @JsonSubTypes.Type(value = EntityResolutionTransformation.class, name = "entity_resolution"),
        @JsonSubTypes.Type(value = LlmTransformation.class, name = "llm"),
})
public sealed interface Transformation
        permits EntityResolutionTransformation, LlmTransformation {

    /** Stable id (server-generated when unset). */
    String id();

    /** Schema-side group name this transformation reads from. */
    String targetGroup();

    /** Optional rename of the output group. {@code null} means replace in place. */
    String outputGroup();

    /** {@code task} (per-document) or {@code request} (across all documents). */
    TransformationScope scope();
}
