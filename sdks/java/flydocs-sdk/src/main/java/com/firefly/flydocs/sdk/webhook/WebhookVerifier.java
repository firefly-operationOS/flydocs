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

package com.firefly.flydocs.sdk.webhook;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Verify {@code X-Flydocs-Signature} HMAC-SHA256 signatures.
 *
 * <p>Constant-time comparison via {@link MessageDigest#isEqual}. Accepts
 * both {@code "sha256=<hex>"} and a bare {@code "<hex>"} form because
 * some intermediate proxies strip the scheme prefix.</p>
 *
 * <pre>{@code
 * WebhookVerifier verifier = new WebhookVerifier(secret);
 * try {
 *     verifier.verify(rawBody, request.getHeader("X-Flydocs-Signature"));
 * } catch (WebhookVerificationException e) {
 *     return ResponseEntity.status(403).build();
 * }
 * }</pre>
 */
public class WebhookVerifier {
    private static final String SCHEME = "sha256";
    private static final String ALGORITHM = "HmacSHA256";

    private final byte[] secret;

    public WebhookVerifier(String secret) {
        if (secret == null || secret.isEmpty()) {
            throw new IllegalArgumentException("HMAC secret cannot be empty");
        }
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
    }

    /**
     * Compute the canonical {@code "sha256=<hex>"} signature value for
     * the given body. Useful for tests and for asserting parity with the
     * service. Don't put this on a public endpoint; the result is the
     * signature itself, not a verifier.
     */
    public String sign(byte[] body) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(new SecretKeySpec(secret, ALGORITHM));
            return SCHEME + '=' + toHex(mac.doFinal(body));
        } catch (NoSuchAlgorithmException | java.security.InvalidKeyException e) {
            // HmacSHA256 is guaranteed by the JDK; surface as runtime so
            // callers don't have to swallow a checked exception that
            // cannot fire in practice.
            throw new IllegalStateException("HmacSHA256 unavailable on this JDK", e);
        }
    }

    /**
     * Throw {@link WebhookVerificationException} if {@code signatureHeader}
     * does not match the HMAC of {@code body}. Return cleanly otherwise.
     *
     * <p>The body must be the raw bytes the service sent — re-encoding
     * the JSON before calling will change the digest and the
     * verification will fail.</p>
     */
    public void verify(byte[] body, String signatureHeader) {
        if (signatureHeader == null || signatureHeader.isEmpty()) {
            throw new WebhookVerificationException("signature header missing");
        }
        String candidate;
        int equals = signatureHeader.indexOf('=');
        if (equals >= 0) {
            String scheme = signatureHeader.substring(0, equals);
            if (!SCHEME.equalsIgnoreCase(scheme)) {
                throw new WebhookVerificationException("unsupported signature scheme: " + scheme);
            }
            candidate = signatureHeader.substring(equals + 1).trim();
        } else {
            candidate = signatureHeader.trim();
        }
        String expected;
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(new SecretKeySpec(secret, ALGORITHM));
            expected = toHex(mac.doFinal(body));
        } catch (NoSuchAlgorithmException | java.security.InvalidKeyException e) {
            throw new IllegalStateException("HmacSHA256 unavailable on this JDK", e);
        }
        if (!MessageDigest.isEqual(
                candidate.getBytes(StandardCharsets.US_ASCII),
                expected.getBytes(StandardCharsets.US_ASCII))) {
            throw new WebhookVerificationException("signature mismatch");
        }
    }

    private static String toHex(byte[] data) {
        StringBuilder sb = new StringBuilder(data.length * 2);
        for (byte b : data) {
            sb.append(Character.forDigit((b >>> 4) & 0xF, 16));
            sb.append(Character.forDigit(b & 0xF, 16));
        }
        return sb.toString();
    }
}
