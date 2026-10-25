package net.batlab.pdfgateway.api;

import java.io.IOException;
import java.time.Duration;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/** Endpoints the web UI needs beyond upload: lists, findings, live events, quarantine, stats, health. */
@RestController
public class UiController {
    private static final Set<String> TERMINAL = Set.of("READY", "REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED");
    private static final Duration STREAM_LIMIT = Duration.ofMinutes(10);
    // Few, cheap polls (one indexed row per second per open stream); bounded threads.
    private static final ScheduledExecutorService POLLER = Executors.newScheduledThreadPool(2, Thread.ofVirtual().factory());
    private final ScanService scans;
    private final InsightService insights;

    public UiController(ScanService scans, InsightService insights) {
        this.scans = scans;
        this.insights = insights;
    }

    @GetMapping("/api/v1/scans")
    ScanService.ScanPage list(@AuthenticationPrincipal GatewayPrincipal principal,
            @RequestParam(defaultValue = "50") int limit, @RequestParam(required = false) String cursor) {
        return scans.list(principal, limit, cursor, false);
    }

    @GetMapping(path = "/api/v1/scans/{scanId}/findings", produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<String> findings(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId) {
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(insights.findings(principal, scanId));
    }

    /** Server-sent status changes until the scan is terminal; clients fall back to polling. */
    @GetMapping(path = "/api/v1/scans/{scanId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    SseEmitter events(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId) {
        ScanView first = scans.get(principal, scanId);  // authorises before any stream opens
        SseEmitter emitter = new SseEmitter(STREAM_LIMIT.toMillis());
        AtomicReference<String> last = new AtomicReference<>("");
        AtomicReference<ScheduledFuture<?>> task = new AtomicReference<>();
        Runnable poll = () -> {
            try {
                ScanView view = scans.get(principal, scanId);
                String key = view.status() + "|" + view.stage() + "|" + view.updatedAt();
                if (!key.equals(last.getAndSet(key))) emitter.send(SseEmitter.event().name("status").data(view, MediaType.APPLICATION_JSON));
                if (TERMINAL.contains(view.status())) emitter.complete();
            } catch (IOException | RuntimeException closed) {
                emitter.completeWithError(closed);
            }
        };
        emitter.onCompletion(() -> { if (task.get() != null) task.get().cancel(false); });
        emitter.onTimeout(emitter::complete);
        emitter.onError(error -> { if (task.get() != null) task.get().cancel(false); });
        task.set(POLLER.scheduleWithFixedDelay(poll, 0, 1, TimeUnit.SECONDS));
        return emitter;
    }

    @GetMapping("/api/v1/quarantine")
    ScanService.ScanPage quarantine(@AuthenticationPrincipal GatewayPrincipal principal,
            @RequestParam(defaultValue = "50") int limit, @RequestParam(required = false) String cursor) {
        return scans.list(principal, limit, cursor, true);
    }

    @DeleteMapping("/api/v1/quarantine/{scanId}")
    @PreAuthorize("hasRole('ADMIN')")
    ResponseEntity<Void> deleteQuarantined(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId,
            @RequestParam(defaultValue = "false") boolean confirmSigned) throws IOException {
        insights.deleteQuarantined(principal, scanId, confirmSigned);
        return ResponseEntity.noContent().build();
    }

    @GetMapping(path = "/api/v1/dashboard/summary", produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<String> summary(@AuthenticationPrincipal GatewayPrincipal principal) {
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(insights.summary(principal));
    }

    @GetMapping(path = "/api/v1/system/health", produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<String> health() {
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(insights.health());
    }
}
