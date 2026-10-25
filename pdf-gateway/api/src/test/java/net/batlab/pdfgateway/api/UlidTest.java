package net.batlab.pdfgateway.api;

import static org.assertj.core.api.Assertions.assertThat;
import org.junit.jupiter.api.Test;

class UlidTest {
    @Test
    void createsOpaqueContractCompatibleIdentifiers() {
        String first = Ulid.next("scan-");
        String second = Ulid.next("scan-");
        assertThat(first).matches("^scan-[0-9A-HJKMNP-TV-Z]{26}$");
        assertThat(second).isNotEqualTo(first);
    }
}
