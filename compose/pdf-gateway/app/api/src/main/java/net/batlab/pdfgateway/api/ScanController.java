package net.batlab.pdfgateway.api;

import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/v1/scans")
public class ScanController {
    private final ScanService scans;
    public ScanController(ScanService scans) { this.scans = scans; }

    @PostMapping(consumes = "multipart/form-data")
    @PreAuthorize("hasAnyRole('ADMIN','USER')")
    ResponseEntity<ScanAccepted> create(@AuthenticationPrincipal GatewayPrincipal principal,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestPart("file") MultipartFile file,
            @RequestParam String intent,
            @RequestParam(defaultValue = "RASTER") String cdrMode,
            HttpServletRequest request) throws IOException {
        String trace = request.getHeader("X-Request-ID");
        if (trace == null || trace.isBlank() || trace.length() > 80) trace = Ulid.next("req-");
        ScanAccepted result = scans.create(principal, idempotencyKey, file, intent, cdrMode, trace);
        return ResponseEntity.accepted().header("Location", result.statusUrl()).body(result);
    }

    @GetMapping("/{scanId}")
    ScanView get(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId) {
        return scans.get(principal, scanId);
    }
}
