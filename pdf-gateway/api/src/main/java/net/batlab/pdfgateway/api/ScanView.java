package net.batlab.pdfgateway.api;

import java.time.OffsetDateTime;

public record ScanView(String scanId, String status, String detection, String policy,
                       String artifact, String importStatus, long sourceBytes,
                       String sourceSha256, OffsetDateTime createdAt, OffsetDateTime updatedAt) {}
