package net.batlab.pdfgateway.api;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.authorization.AuthorizationDeniedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.resource.NoResourceFoundException;

@RestControllerAdvice
class ProblemHandler {
    private static final Logger log = LoggerFactory.getLogger(ProblemHandler.class);

    @ExceptionHandler(ApiException.class)
    ResponseEntity<Problem> api(ApiException error, HttpServletRequest request) {
        String trace = Optional.ofNullable(request.getHeader("X-Request-ID")).filter(v -> v.length() <= 80).orElse("unavailable");
        return ResponseEntity.status(error.status).contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new Problem("about:blank", error.code, error.status, error.getMessage(), error.code, trace));
    }

    @ExceptionHandler(NoResourceFoundException.class)
    ResponseEntity<Problem> notFound(NoResourceFoundException error, HttpServletRequest request) {
        String trace = Optional.ofNullable(request.getHeader("X-Request-ID")).filter(v -> v.length() <= 80).orElse("unavailable");
        return ResponseEntity.status(404).contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new Problem("about:blank", "Not found", 404,
                        "The requested resource does not exist", "NOT_FOUND", trace));
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    ResponseEntity<Problem> methodNotAllowed(HttpRequestMethodNotSupportedException error, HttpServletRequest request) {
        String trace = Optional.ofNullable(request.getHeader("X-Request-ID")).filter(v -> v.length() <= 80).orElse("unavailable");
        return ResponseEntity.status(405).contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new Problem("about:blank", "Method not allowed", 405,
                        "This resource does not support that method", "METHOD_NOT_ALLOWED", trace));
    }

    @ExceptionHandler(AuthorizationDeniedException.class)
    ResponseEntity<Problem> forbidden(AuthorizationDeniedException error, HttpServletRequest request) {
        String trace = Optional.ofNullable(request.getHeader("X-Request-ID")).filter(v -> v.length() <= 80).orElse("unavailable");
        return ResponseEntity.status(403).contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new Problem("about:blank", "Forbidden", 403,
                        "This account is not allowed to perform that operation", "FORBIDDEN", trace));
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<Problem> unexpected(Exception error, HttpServletRequest request) {
        String trace = Optional.ofNullable(request.getHeader("X-Request-ID")).filter(v -> v.length() <= 80).orElse("unavailable");
        log.error("Unhandled API error traceId={}", trace, error);
        return ResponseEntity.internalServerError().contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new Problem("about:blank", "Internal server error", 500,
                        "The request could not be completed", "INTERNAL_ERROR", trace));
    }
}
