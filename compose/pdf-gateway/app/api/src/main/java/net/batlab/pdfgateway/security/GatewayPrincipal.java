package net.batlab.pdfgateway.security;

import java.util.UUID;

public record GatewayPrincipal(UUID userId, String username, String role) {
    public boolean admin() { return "ADMIN".equals(role); }
}
