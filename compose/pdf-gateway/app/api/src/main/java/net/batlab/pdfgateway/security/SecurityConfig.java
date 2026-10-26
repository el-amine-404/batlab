package net.batlab.pdfgateway.security;

import jakarta.servlet.http.HttpServletResponse;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

@Configuration
@EnableMethodSecurity
public class SecurityConfig {
    @Bean
    SecurityFilterChain security(HttpSecurity http, BearerTokenFilter tokens) throws Exception {
        return http
                // CSRF for cookie sessions is enforced in BearerTokenFilter (per-session token
                // plus Origin check); bearer-token requests are not ambient and need none.
                .csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        // SSE completes on an async dispatch; the request was authorised on entry.
                        .dispatcherTypeMatchers(jakarta.servlet.DispatcherType.ASYNC).permitAll()
                        .requestMatchers("/internal/actuator/health/**").permitAll()
                        .requestMatchers(org.springframework.http.HttpMethod.POST, "/api/v1/session", "/api/v1/session/setup").permitAll()
                        .requestMatchers(org.springframework.http.HttpMethod.GET, "/api/v1/session/setup").permitAll()
                        // Allow Spring to render the original failure. Otherwise an
                        // ERROR dispatch without the bearer header masks a 500 as 401.
                        .requestMatchers("/error").permitAll()
                        .requestMatchers("/internal/**").denyAll()
                        // The AI laptop's token may use the enrichment endpoints and nothing else.
                        .requestMatchers("/api/v1/enrichment/**").hasRole("ENRICHMENT")
                        .anyRequest().access(SecurityConfig::authenticatedNotEnrichment))
                .exceptionHandling(errors -> errors.authenticationEntryPoint((request, response, failure) -> {
                    response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
                    response.setContentType("application/problem+json");
                    response.getWriter().write("{\"type\":\"about:blank\",\"title\":\"Unauthorized\",\"status\":401,\"code\":\"AUTH_REQUIRED\",\"traceId\":\"unavailable\"}");
                }))
                .addFilterBefore(tokens, UsernamePasswordAuthenticationFilter.class)
                .build();
    }

    private static org.springframework.security.authorization.AuthorizationDecision authenticatedNotEnrichment(
            java.util.function.Supplier<? extends org.springframework.security.core.Authentication> authentication,
            org.springframework.security.web.access.intercept.RequestAuthorizationContext context) {
        var auth = authentication.get();
        boolean allowed = auth != null && auth.isAuthenticated()
                && !(auth instanceof org.springframework.security.authentication.AnonymousAuthenticationToken)
                && auth.getAuthorities().stream().noneMatch(a -> "ROLE_ENRICHMENT".equals(a.getAuthority()));
        return new org.springframework.security.authorization.AuthorizationDecision(allowed);
    }
}
