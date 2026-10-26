package net.batlab.pdfgateway.api;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Map;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/archive")
public class ArchiveController {
    private final ArchiveService archive;
    public ArchiveController(ArchiveService archive) { this.archive = archive; }

    @GetMapping
    ArchiveService.DocumentPage list(@AuthenticationPrincipal GatewayPrincipal principal,
            @RequestParam(defaultValue = "50") int limit, @RequestParam(required = false) String cursor) {
        return archive.list(principal, limit, cursor);
    }

    @GetMapping("/{documentId}")
    ArchiveService.DocumentView get(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId) {
        return archive.locate(principal, documentId).view();
    }

    @GetMapping("/{documentId}/metadata")
    ResponseEntity<byte[]> metadata(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId) throws IOException {
        ArchiveService.Sidecar sidecar = archive.sidecar(principal, documentId);
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).eTag("\"rev-" + sidecar.revision() + "\"")
                .header("X-Content-Type-Options", "nosniff").header(HttpHeaders.CACHE_CONTROL, "private, no-store")
                .body(sidecar.bytes());
    }

    @GetMapping("/{documentId}/sidecar")
    ResponseEntity<byte[]> sidecar(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId) throws IOException {
        ArchiveService.Sidecar sidecar = archive.sidecar(principal, documentId);
        return download(ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).eTag("\"rev-" + sidecar.revision() + "\""),
                "metadata.json").body(sidecar.bytes());
    }

    @GetMapping("/{documentId}/file")
    ResponseEntity<FileSystemResource> file(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId,
                                            @RequestParam(defaultValue = "false") boolean view) throws IOException {
        Path pdf = archive.verifiedPdf(principal, documentId);
        if (view) {
            // The clean copy only (validated: page images and an invisible text layer, no scripts, links
            // or forms), shown by the browser's own PDF viewer; never framed, sniffed or cached.
            return ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF)
                    .header(HttpHeaders.CONTENT_DISPOSITION, "inline; filename=\"" + pdf.getFileName().toString().replaceAll("[^A-Za-z0-9._ -]", "_") + "\"")
                    .header("X-Content-Type-Options", "nosniff")
                    .header("Content-Security-Policy", "default-src 'none'; object-src 'none'; frame-ancestors 'none'")
                    .header(HttpHeaders.CACHE_CONTROL, "private, no-store")
                    .body(new FileSystemResource(pdf));
        }
        return download(ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF), pdf.getFileName().toString())
                .body(new FileSystemResource(pdf));
    }

    @PatchMapping(path = "/{documentId}/metadata", consumes = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasAnyRole('ADMIN','USER')")
    ResponseEntity<ArchiveService.EditView> edit(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId,
            @RequestHeader(value = HttpHeaders.IF_MATCH, required = false) String ifMatch,
            @RequestBody Map<String, Object> changes) throws IOException {
        ArchiveService.EditView edit = archive.requestEdit(principal, documentId, ifMatch, changes);
        return ResponseEntity.accepted().header(HttpHeaders.LOCATION, edit.statusUrl()).body(edit);
    }

    @GetMapping("/{documentId}/edits/{editId}")
    ArchiveService.EditView editStatus(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId,
            @PathVariable String editId) {
        return archive.edit(principal, documentId, editId);
    }

    /** Downloads: never rendered inline, sniffed or cached. */
    private static ResponseEntity.BodyBuilder download(ResponseEntity.BodyBuilder builder, String filename) {
        String ascii = filename.replaceAll("[^A-Za-z0-9._ -]", "_");
        String encoded = URLEncoder.encode(filename, StandardCharsets.UTF_8).replace("+", "%20");
        return builder.header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + ascii + "\"; filename*=UTF-8''" + encoded)
                .header("X-Content-Type-Options", "nosniff")
                .header("Content-Security-Policy", "sandbox; default-src 'none'")
                .header(HttpHeaders.CACHE_CONTROL, "private, no-store");
    }
}
