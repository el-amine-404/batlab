package net.batlab.pdfgateway.api;

public final class ApiException extends RuntimeException {
    final int status;
    final String code;
    /** An API path the client may follow (sent as Location), or null. */
    final String location;
    public ApiException(int status, String code, String message) { this(status, code, message, null); }
    public ApiException(int status, String code, String message, String location) {
        super(message); this.status = status; this.code = code; this.location = location;
    }
}
