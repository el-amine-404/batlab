package net.batlab.pdfgateway.api;

import java.time.OffsetDateTime;

/** Public scan representation; every enum field carries OpenAPI contract values. */
public record ScanView(String scanId, String status, String stage, String detection, String policy,
                       String artifact, String originalRetention, String importStatus, long sourceBytes,
                       String sourceSha256, OffsetDateTime createdAt, OffsetDateTime updatedAt) {}
