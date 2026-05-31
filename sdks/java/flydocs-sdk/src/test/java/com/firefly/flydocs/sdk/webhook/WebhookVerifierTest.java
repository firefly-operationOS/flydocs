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

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

/**
 * Webhook verifier tests.
 *
 * <p>Covers the three failure modes the SDK promises to detect:
 * missing header, wrong scheme prefix, digest mismatch. Plus a
 * roundtrip with {@link WebhookVerifier#sign(byte[])} so the two
 * helpers can't drift apart silently.</p>
 */
class WebhookVerifierTest {

    private static final byte[] BODY = "{\"job_id\":\"job1\",\"status\":\"SUCCEEDED\"}".getBytes(StandardCharsets.UTF_8);

    @Test
    void sign_then_verify_roundtrips() {
        WebhookVerifier v = new WebhookVerifier("topsecret");
        String sig = v.sign(BODY);
        assertThat(sig).startsWith("sha256=");
        // Doesn't throw — verification succeeded.
        v.verify(BODY, sig);
    }

    @Test
    void verify_accepts_bare_hex_without_scheme_prefix() {
        WebhookVerifier v = new WebhookVerifier("topsecret");
        String sig = v.sign(BODY);
        String bare = sig.substring("sha256=".length());
        v.verify(BODY, bare);
    }

    @Test
    void verify_rejects_missing_header() {
        WebhookVerifier v = new WebhookVerifier("topsecret");
        assertThatThrownBy(() -> v.verify(BODY, ""))
                .isInstanceOf(WebhookVerificationException.class)
                .hasMessageContaining("signature header missing");
    }

    @Test
    void verify_rejects_wrong_scheme() {
        WebhookVerifier v = new WebhookVerifier("topsecret");
        assertThatThrownBy(() -> v.verify(BODY, "md5=deadbeef"))
                .isInstanceOf(WebhookVerificationException.class)
                .hasMessageContaining("unsupported signature scheme");
    }

    @Test
    void verify_rejects_digest_mismatch() {
        WebhookVerifier v = new WebhookVerifier("topsecret");
        // 64 zeros = valid hex length, but won't match.
        assertThatThrownBy(() -> v.verify(BODY, "sha256=" + "0".repeat(64)))
                .isInstanceOf(WebhookVerificationException.class)
                .hasMessageContaining("signature mismatch");
    }

    @Test
    void empty_secret_rejected_at_construction() {
        assertThatThrownBy(() -> new WebhookVerifier(""))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void same_secret_produces_same_digest_across_instances() {
        WebhookVerifier a = new WebhookVerifier("s");
        WebhookVerifier b = new WebhookVerifier("s");
        assertThat(a.sign(BODY)).isEqualTo(b.sign(BODY));
    }
}
