package net.batlab.pdfgateway.api;

import java.util.Map;
import java.util.Set;

/**
 * Maps internal pipeline values to the public OpenAPI vocabulary. Workers keep
 * finer internal states; the exact one is exposed separately as {@code stage}.
 * Anything unrecognised maps to the most conservative public value, never to
 * a release state.
 */
final class ScanContract {
    private static final Set<String> STATUSES = Set.of("RECEIVED", "QUARANTINED", "QUEUED", "SCANNING", "ANALYZING",
            "AWAITING_POLICY", "SANITIZING", "VALIDATING_OUTPUT", "ARCHIVING", "READY", "REVIEW_REQUIRED", "BLOCKED",
            "FAILED", "CANCELLED");
    private static final Map<String, String> STATUS_ALIASES = Map.of(
            "AWAITING_CDR", "SANITIZING",
            "AWAITING_ARCHIVE", "ARCHIVING");
    private static final Set<String> DETECTIONS = Set.of("NO_THREATS_DETECTED", "SUSPICIOUS", "MALICIOUS", "UNKNOWN");
    private static final Set<String> POLICIES = Set.of("ALLOW_SANITIZED", "REVIEW_REQUIRED", "BLOCK", "CDR_ELIGIBLE");
    private static final Set<String> ARTIFACTS = Set.of("ORIGINAL_QUARANTINED", "SANITIZED_STAGED", "FINAL_VALIDATED", "PUBLISHED");
    private static final Set<String> RETENTIONS = Set.of("STANDARD", "SIGNED_RESTRICTED");

    private ScanContract() {}

    static String status(String internal) {
        // Immutable Set/Map lookups reject null, so it is handled first.
        if (internal == null) return "REVIEW_REQUIRED";
        if (STATUSES.contains(internal)) return internal;
        return STATUS_ALIASES.getOrDefault(internal, "REVIEW_REQUIRED");
    }

    static String detection(String internal) {
        // M2 analysis reports use CLEAN for "no engine found anything".
        if ("CLEAN".equals(internal)) return "NO_THREATS_DETECTED";
        return internal != null && DETECTIONS.contains(internal) ? internal : "UNKNOWN";
    }

    static String policy(String internal) {
        return internal != null && POLICIES.contains(internal) ? internal : "REVIEW_REQUIRED";
    }

    static String artifact(String internal) {
        return internal != null && ARTIFACTS.contains(internal) ? internal : "ORIGINAL_QUARANTINED";
    }

    static String retention(String internal) {
        // Unknown retention is treated as restricted: an original is never under-protected.
        return internal != null && RETENTIONS.contains(internal) ? internal : "SIGNED_RESTRICTED";
    }

    static String stage(String internal) {
        return internal != null && internal.matches("^[A-Z][A-Z_]{2,31}$") ? internal : "UNKNOWN";
    }
}
