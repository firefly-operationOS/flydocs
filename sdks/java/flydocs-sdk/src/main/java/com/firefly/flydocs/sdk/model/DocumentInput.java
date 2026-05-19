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

package com.firefly.flydocs.sdk.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import org.jspecify.annotations.Nullable;

/**
 * One input file for an extraction request.
 *
 * <p>{@code contentBase64} is sent on the wire as {@code content_base64};
 * Jackson handles the snake_case mapping via the {@link JsonProperty}
 * annotation. The {@link #ofBytes} / {@link #ofPath} factories handle
 * base64 encoding for callers that don't want to fiddle with
 * {@link Base64} themselves.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record DocumentInput(
        String filename,
        @JsonProperty("content_base64") String contentBase64,
        @JsonProperty("content_type") @Nullable String contentType,
        @JsonProperty("document_type") @Nullable String documentType) {

    /** Build a {@link DocumentInput} from raw bytes (handles base64 encoding). */
    public static DocumentInput ofBytes(byte[] data, String filename) {
        return ofBytes(data, filename, null, null);
    }

    /** Build a {@link DocumentInput} from raw bytes with optional content/document type hints. */
    public static DocumentInput ofBytes(
            byte[] data,
            String filename,
            @Nullable String contentType,
            @Nullable String documentType) {
        return new DocumentInput(
                filename,
                Base64.getEncoder().encodeToString(data),
                contentType,
                documentType);
    }

    /** Read a file off disk and produce a {@link DocumentInput}. */
    public static DocumentInput ofPath(Path path) throws IOException {
        return ofPath(path, null, null);
    }

    /** Read a file off disk with optional content/document type hints. */
    public static DocumentInput ofPath(
            Path path,
            @Nullable String contentType,
            @Nullable String documentType) throws IOException {
        return ofBytes(Files.readAllBytes(path), path.getFileName().toString(), contentType, documentType);
    }
}
