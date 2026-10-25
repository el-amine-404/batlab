package net.batlab.pdfgateway.config;

import java.nio.file.Path;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("pdf-gateway")
public record GatewayProperties(
        Path storageRoot,
        Path archiveRoot,
        long maxUploadBytes,
        long minimumFreeBytes,
        int minimumFreePercent,
        String bootstrapAdminName,
        String bootstrapAdminToken) {
}
