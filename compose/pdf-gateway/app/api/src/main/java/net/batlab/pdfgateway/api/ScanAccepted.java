package net.batlab.pdfgateway.api;

public record ScanAccepted(String scanId, String status, String statusUrl, String eventsUrl) {}
