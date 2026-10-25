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
                .csrf(csrf -> csrf.disable()) // bearer API only in M1; cookie UI adds CSRF in M5
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/internal/actuator/health/**").permitAll()
                        // Allow Spring to render the original failure. Otherwise an
                        // ERROR dispatch without the bearer header masks a 500 as 401.
                        .requestMatchers("/error").permitAll()
                        .requestMatchers("/internal/**").denyAll()
                        .anyRequest().authenticated())
                .exceptionHandling(errors -> errors.authenticationEntryPoint((request, response, failure) -> {
                    response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
                    response.setContentType("application/problem+json");
                    response.getWriter().write("{\"type\":\"about:blank\",\"title\":\"Unauthorized\",\"status\":401,\"code\":\"AUTH_REQUIRED\",\"traceId\":\"unavailable\"}");
                }))
                .addFilterBefore(tokens, UsernamePasswordAuthenticationFilter.class)
                .build();
    }
}
