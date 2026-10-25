package net.batlab.pdfgateway.api;

import java.security.SecureRandom;

final class Ulid {
    private static final char[] ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ".toCharArray();
    private static final SecureRandom RANDOM = new SecureRandom();
    private Ulid() {}

    static String next(String prefix) {
        byte[] bytes = new byte[16];
        long time = System.currentTimeMillis();
        bytes[0] = (byte) (time >>> 40); bytes[1] = (byte) (time >>> 32);
        bytes[2] = (byte) (time >>> 24); bytes[3] = (byte) (time >>> 16);
        bytes[4] = (byte) (time >>> 8); bytes[5] = (byte) time;
        byte[] random = new byte[10]; RANDOM.nextBytes(random);
        System.arraycopy(random, 0, bytes, 6, 10);
        char[] out = new char[26];
        int buffer = 0, bits = 0, index = 0;
        for (byte value : bytes) {
            buffer = (buffer << 8) | (value & 255); bits += 8;
            while (bits >= 5 && index < 26) { bits -= 5; out[index++] = ALPHABET[(buffer >>> bits) & 31]; }
        }
        if (index < 26) out[index++] = ALPHABET[(buffer << (5 - bits)) & 31];
        while (index < 26) out[index++] = ALPHABET[0];
        return prefix + new String(out);
    }
}
