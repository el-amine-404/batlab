package net.batlab.pdfgateway.security;

import static org.assertj.core.api.Assertions.assertThat;
import org.junit.jupiter.api.Test;

class TokenHasherTest {
    @Test
    void hashesUtf8DeterministicallyWithoutReturningTheSecret() {
        String hash = TokenHasher.sha256("correct horse battery staple");
        assertThat(hash).hasSize(64).isEqualTo("c4bbcb1fbec99d65bf59d85c8cb62ee2db963f0fe106f483d9afa73bd4e39a8a");
        assertThat(hash).doesNotContain("horse");
    }
}
