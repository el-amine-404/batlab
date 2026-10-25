package net.batlab.pdfgateway.api;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import org.junit.jupiter.api.Test;

class ScanContractTest {
    @Test
    void mapsInternalPipelineStatesToContractValues() {
        assertThat(ScanContract.status("AWAITING_CDR")).isEqualTo("SANITIZING");
        assertThat(ScanContract.status("AWAITING_ARCHIVE")).isEqualTo("ARCHIVING");
        assertThat(ScanContract.status("VALIDATING_OUTPUT")).isEqualTo("VALIDATING_OUTPUT");
        assertThat(ScanContract.stage("AWAITING_CDR")).isEqualTo("AWAITING_CDR");
        assertThat(ScanContract.detection("CLEAN")).isEqualTo("NO_THREATS_DETECTED");
        assertThat(ScanContract.policy("CDR_ELIGIBLE")).isEqualTo("CDR_ELIGIBLE");
        assertThat(ScanContract.artifact("SIGNED_ORIGINAL_RESTRICTED")).isEqualTo("ORIGINAL_QUARANTINED");
    }

    @Test
    void unknownValuesNeverBecomeReleaseStates() {
        for (String value : List.of("", "SOMETHING_NEW", "ready", "<script>")) {
            assertThat(ScanContract.status(value)).isEqualTo("REVIEW_REQUIRED");
            assertThat(ScanContract.detection(value)).isEqualTo("UNKNOWN");
            assertThat(ScanContract.policy(value)).isEqualTo("REVIEW_REQUIRED");
            assertThat(ScanContract.artifact(value)).isEqualTo("ORIGINAL_QUARANTINED");
            assertThat(ScanContract.retention(value)).isEqualTo("SIGNED_RESTRICTED");
        }
        assertThat(ScanContract.stage("<script>")).isEqualTo("UNKNOWN");
        assertThat(ScanContract.status(null)).isEqualTo("REVIEW_REQUIRED");
        assertThat(ScanContract.detection(null)).isEqualTo("UNKNOWN");
        assertThat(ScanContract.policy(null)).isEqualTo("REVIEW_REQUIRED");
        assertThat(ScanContract.retention(null)).isEqualTo("SIGNED_RESTRICTED");
        assertThat(ScanContract.stage(null)).isEqualTo("UNKNOWN");
    }
}
