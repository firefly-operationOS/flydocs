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

package com.firefly.flydocs.sdk.spring;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.firefly.flydocs.sdk.model.EventEnvelope;
import com.firefly.flydocs.sdk.webhook.WebhookVerificationException;
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import org.springframework.core.MethodParameter;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/**
 * Servlet-stack {@link HandlerMethodArgumentResolver} for the
 * {@link FlydocsWebhook} annotation.
 *
 * <p>Resolves a controller method parameter typed
 * {@link EventEnvelope} by verifying the HMAC signature header and
 * deserialising the raw request bytes.</p>
 */
public class FlydocsWebhookArgumentResolver implements HandlerMethodArgumentResolver {

    /** Header the service signs the body with. */
    public static final String SIGNATURE_HEADER = "X-Flydocs-Signature";

    private final WebhookVerifier verifier;
    private final ObjectMapper mapper;

    public FlydocsWebhookArgumentResolver(WebhookVerifier verifier, ObjectMapper mapper) {
        this.verifier = verifier;
        this.mapper = mapper;
    }

    @Override
    public boolean supportsParameter(MethodParameter parameter) {
        return parameter.hasParameterAnnotation(FlydocsWebhook.class)
                && EventEnvelope.class.isAssignableFrom(parameter.getParameterType());
    }

    @Override
    public Object resolveArgument(
            MethodParameter parameter,
            ModelAndViewContainer mavContainer,
            NativeWebRequest webRequest,
            WebDataBinderFactory binderFactory) throws IOException {
        HttpServletRequest request = webRequest.getNativeRequest(HttpServletRequest.class);
        if (request == null) {
            throw new IllegalStateException("FlydocsWebhook resolver requires a servlet request");
        }
        byte[] body = request.getInputStream().readAllBytes();
        String signature = request.getHeader(SIGNATURE_HEADER);
        if (signature == null) {
            throw new WebhookVerificationException("missing " + SIGNATURE_HEADER + " header");
        }
        verifier.verify(body, signature);
        return mapper.readValue(body, EventEnvelope.class);
    }
}
