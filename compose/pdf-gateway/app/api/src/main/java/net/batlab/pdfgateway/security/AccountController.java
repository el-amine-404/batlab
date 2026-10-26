package net.batlab.pdfgateway.security;

import java.util.List;
import java.util.UUID;
import net.batlab.pdfgateway.api.ApiException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Admins: the people and apps that may use the gateway, their roles, and the apps' API keys. */
@RestController
@RequestMapping("/api/v1/accounts")
@PreAuthorize("hasRole('ADMIN')")
public class AccountController {
    private final Accounts accounts;

    public AccountController(Accounts accounts) { this.accounts = accounts; }

    public record AccountIn(String username, String role, String password) {}
    public record AccountPatch(String role, Boolean enabled, String password) {}
    public record KeyIn(String label) {}

    @GetMapping
    ResponseEntity<List<Accounts.Account>> list() {
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(accounts.list());
    }

    /** With a password: a person who signs in on the page. Without: an app, which then gets a key. */
    @PostMapping
    ResponseEntity<Accounts.Account> create(@AuthenticationPrincipal GatewayPrincipal me, @RequestBody AccountIn body) {
        if (body == null) throw new ApiException(422, "BAD_REQUEST", "A name and a role are needed.");
        return ResponseEntity.status(201).body(accounts.create(me.userId(), body.username(), body.role(), body.password()));
    }

    @PatchMapping("/{id}")
    Accounts.Account update(@AuthenticationPrincipal GatewayPrincipal me, @PathVariable UUID id, @RequestBody AccountPatch body) {
        if (body == null) throw new ApiException(422, "BAD_REQUEST", "Nothing to change.");
        return accounts.update(me.userId(), id, body.role(), body.enabled(), body.password());
    }

    /** A new API key: the only time it is shown; only its SHA-256 is kept. */
    @PostMapping("/{id}/keys")
    ResponseEntity<Accounts.NewKey> addKey(@AuthenticationPrincipal GatewayPrincipal me, @PathVariable UUID id, @RequestBody(required = false) KeyIn body) {
        return ResponseEntity.status(201).header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(accounts.addKey(me.userId(), id, body == null ? null : body.label()));
    }

    @DeleteMapping("/{id}/keys/{keyId}")
    ResponseEntity<Void> revokeKey(@AuthenticationPrincipal GatewayPrincipal me, @PathVariable UUID id, @PathVariable UUID keyId) {
        accounts.revokeKey(me.userId(), id, keyId);
        return ResponseEntity.noContent().build();
    }
}
