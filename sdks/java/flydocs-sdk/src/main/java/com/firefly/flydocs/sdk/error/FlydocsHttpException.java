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

package com.firefly.flydocs.sdk.error;

import java.util.Map;
import org.jspecify.annotations.Nullable;

/**
 * The service answered with a 4xx/5xx.
 *
 * <p>flydocs emits RFC 7807-ish problem-details with {@code code},
 * {@code title}, {@code detail} (sometimes nested under a {@code detail}
 * object, sometimes at the top level). The SDK decodes both shapes onto
 * this exception so callers can branch on {@link #code()} regardless of
 * which path the service took.</p>
 */
public class FlydocsHttpException extends FlydocsException {
    private final int statusCode;
    private final String code;
    private final String title;
    private final String detail;
    private final Map<String, Object> payload;
    private final String rawBody;

    public FlydocsHttpException(
            int statusCode,
            @Nullable String code,
            @Nullable String title,
            @Nullable String detail,
            @Nullable Map<String, Object> payload,
            @Nullable String rawBody) {
        super(buildMessage(statusCode, code, detail, title));
        this.statusCode = statusCode;
        this.code = code == null ? "" : code;
        this.title = title == null ? "" : title;
        this.detail = detail == null ? "" : detail;
        this.payload = payload == null ? Map.of() : payload;
        this.rawBody = rawBody == null ? "" : rawBody;
    }

    public int statusCode() {
        return statusCode;
    }

    public String code() {
        return code;
    }

    public String title() {
        return title;
    }

    public String detail() {
        return detail;
    }

    public Map<String, Object> payload() {
        return payload;
    }

    public String rawBody() {
        return rawBody;
    }

    private static String buildMessage(int status, @Nullable String code, @Nullable String detail, @Nullable String title) {
        StringBuilder sb = new StringBuilder("HTTP ").append(status);
        if (code != null && !code.isEmpty()) {
            sb.append(' ').append(code);
        }
        if (detail != null && !detail.isEmpty()) {
            sb.append(": ").append(detail);
        } else if (title != null && !title.isEmpty()) {
            sb.append(": ").append(title);
        }
        return sb.toString();
    }
}
