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
 * <p>JSON mode: caller sets {@code content_base64}. Multipart mode: the
 * binary rides in a separate file part and {@code content_base64} is
 * absent (the part body is the binary).</p>
 *
 * <p>{@code expected_type} is a soft hint that must reference one of the
 * declared {@code document_types[].id} values. When present, the
 * classifier is skipped for this file even if the classifier stage is
 * enabled.</p>
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record FileInput(
        @JsonProperty("filename") String filename,
        @JsonProperty("content_base64") @Nullable String contentBase64,
        @JsonProperty("content_type") @Nullable String contentType,
        @JsonProperty("expected_type") @Nullable String expectedType) {

    /** Build a {@link FileInput} from raw bytes (handles base64 encoding). */
    public static FileInput ofBytes(byte[] data, String filename) {
        return ofBytes(data, filename, null, null);
    }

    /** Build a {@link FileInput} from raw bytes with optional content/expected type hints. */
    public static FileInput ofBytes(
            byte[] data,
            String filename,
            @Nullable String contentType,
            @Nullable String expectedType) {
        return new FileInput(
                filename,
                Base64.getEncoder().encodeToString(data),
                contentType,
                expectedType);
    }

    /** Read a file off disk and produce a {@link FileInput}. */
    public static FileInput ofPath(Path path) throws IOException {
        return ofPath(path, null, null);
    }

    /** Read a file off disk with optional content/expected type hints. */
    public static FileInput ofPath(
            Path path,
            @Nullable String contentType,
            @Nullable String expectedType) throws IOException {
        return ofBytes(Files.readAllBytes(path), path.getFileName().toString(), contentType, expectedType);
    }
}
