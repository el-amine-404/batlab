package net.batlab.pdfgateway.api;

public record Problem(String type, String title, int status, String detail, String code, String traceId) {}
