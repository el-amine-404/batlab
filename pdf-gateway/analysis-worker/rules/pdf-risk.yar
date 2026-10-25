rule PDF_Active_Content_High : pdf active_content {
  meta:
    severity = "HIGH"
    description = "PDF contains an active-action keyword"
    ruleset_version = "pdf-risk-1"
  strings:
    $javascript = /\/JavaScript/i ascii
    $openaction = /\/OpenAction/i ascii
    $launch = /\/Launch/i ascii
    $richmedia = /\/RichMedia/i ascii
  condition:
    uint32be(0) == 0x25504446 and any of them
}

rule PDF_Attachment_Medium : pdf attachment {
  meta:
    severity = "MEDIUM"
    description = "PDF contains an embedded-file keyword"
    ruleset_version = "pdf-risk-1"
  strings:
    $embedded = /\/EmbeddedFile/i ascii
    $filespec = /\/Filespec/i ascii
  condition:
    uint32be(0) == 0x25504446 and any of them
}
