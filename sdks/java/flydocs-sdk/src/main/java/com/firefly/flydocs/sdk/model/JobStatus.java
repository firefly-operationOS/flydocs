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

/**
 * Lifecycle state of an async extraction job.
 *
 * <p>Mirrors {@code flydocs.interfaces.enums.job_status.JobStatus} on the
 * service side. The wire form is the enum constant name, e.g. {@code "QUEUED"}.
 * Unknown future values are surfaced as {@link #UNKNOWN} so an older SDK
 * keeps working when the service ships a new status without a coordinated
 * client release.</p>
 */
public enum JobStatus {
    QUEUED,
    RUNNING,
    SUCCEEDED,
    PARTIAL_SUCCEEDED,
    REFINING_BBOXES,
    FAILED,
    CANCELLED,

    /**
     * Sentinel for any value the SDK does not recognise.
     *
     * <p>Look at {@link JobStatusResponse#errorCode()} or the raw JSON node
     * to discover what the service actually emitted; treating the job as
     * non-terminal until you decide is the safest default.</p>
     */
    UNKNOWN;

    /**
     * Parse the wire-form string into a {@link JobStatus}.
     *
     * <p>Returns {@link #UNKNOWN} for any value the enum does not declare,
     * never throws. Use when deserialising JSON or when reading the
     * {@code status} field of a webhook payload.</p>
     */
    public static JobStatus fromWire(String value) {
        if (value == null || value.isEmpty()) {
            return UNKNOWN;
        }
        try {
            return JobStatus.valueOf(value);
        } catch (IllegalArgumentException ignored) {
            return UNKNOWN;
        }
    }
}
