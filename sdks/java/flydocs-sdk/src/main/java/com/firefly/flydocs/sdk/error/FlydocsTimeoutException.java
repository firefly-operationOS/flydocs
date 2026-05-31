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

package com.firefly.flydocs.sdk.error;

/**
 * The SDK's own HTTP timeout fired before the service answered.
 *
 * <p>Distinct from a {@link FlydocsHttpException} with status 408, which
 * is the service telling the SDK that the extraction pipeline itself
 * timed out and the caller should retry via the async API. This one
 * means the request never came back over the wire.</p>
 */
public class FlydocsTimeoutException extends FlydocsClientException {
    public FlydocsTimeoutException(String message) {
        super(message);
    }

    public FlydocsTimeoutException(String message, Throwable cause) {
        super(message, cause);
    }
}
