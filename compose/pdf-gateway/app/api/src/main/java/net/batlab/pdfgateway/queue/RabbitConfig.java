package net.batlab.pdfgateway.queue;

import java.util.ArrayList;
import java.util.List;
import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.Declarable;
import org.springframework.amqp.core.Declarables;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
class RabbitConfig {
    static final String EXCHANGE = "pdf.gateway.jobs";
    static final String QUEUE = "pdf.gateway.scan.v1";
    static final String SIGNATURE_QUEUE = "pdf.gateway.signature.v1";
    static final String CDR_QUEUE = "pdf.gateway.cdr.v1";
    static final String VALIDATION_QUEUE = "pdf.gateway.validation.v1";
    // No consumer until M4; the durable queue holds validated jobs for the archive publisher.
    static final String ARCHIVE_QUEUE = "pdf.gateway.archive.v1";
    static final String METADATA_EDIT_QUEUE = "pdf.gateway.metadata-edit.v1";
    static final String PAPERLESS_QUEUE = "pdf.gateway.paperless.v1";
    static final String ENRICHMENT_QUEUE = "pdf.gateway.enrichment-result.v1";
    // A new version of a published document: final validation again, then the archive swaps it in.
    static final String REVISION_VALIDATION_QUEUE = "pdf.gateway.revision-validation.v1";
    static final String REVISION_PUBLISH_QUEUE = "pdf.gateway.revision-publish.v1";
    // A deleted document: each stage removes its own files (one queue each, same event).
    static final String[] DELETED_STAGES = {"analysis", "validation", "signature", "cdr", "archive", "paperless"};
    @Bean DirectExchange jobExchange() { return new DirectExchange(EXCHANGE, true, false); }
    @Bean Queue scanQueue() { return new Queue(QUEUE, true, false, false); }
    @Bean Binding scanBinding(Queue scanQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(scanQueue).to(jobExchange).with("SCAN_QUEUED");
    }
    @Bean Queue signatureQueue() { return new Queue(SIGNATURE_QUEUE, true, false, false); }
    @Bean Binding signatureBinding(Queue signatureQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(signatureQueue).to(jobExchange).with("SIGNATURE_QUEUED");
    }
    @Bean Queue cdrQueue() { return new Queue(CDR_QUEUE, true, false, false); }
    @Bean Binding cdrBinding(Queue cdrQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(cdrQueue).to(jobExchange).with("CDR_QUEUED");
    }
    @Bean Queue validationQueue() { return new Queue(VALIDATION_QUEUE, true, false, false); }
    @Bean Binding validationBinding(Queue validationQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(validationQueue).to(jobExchange).with("VALIDATION_QUEUED");
    }
    @Bean Queue archiveQueue() { return new Queue(ARCHIVE_QUEUE, true, false, false); }
    @Bean Binding archiveBinding(Queue archiveQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(archiveQueue).to(jobExchange).with("ARCHIVE_QUEUED");
    }
    @Bean Queue metadataEditQueue() { return new Queue(METADATA_EDIT_QUEUE, true, false, false); }
    @Bean Binding metadataEditBinding(Queue metadataEditQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(metadataEditQueue).to(jobExchange).with("METADATA_EDIT_QUEUED");
    }
    @Bean Queue paperlessQueue() { return new Queue(PAPERLESS_QUEUE, true, false, false); }
    @Bean Binding paperlessBinding(Queue paperlessQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(paperlessQueue).to(jobExchange).with("PAPERLESS_QUEUED");
    }
    @Bean Queue enrichmentQueue() { return new Queue(ENRICHMENT_QUEUE, true, false, false); }
    @Bean Binding enrichmentBinding(Queue enrichmentQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(enrichmentQueue).to(jobExchange).with("ENRICHMENT_RESULT_QUEUED");
    }
    @Bean Queue revisionValidationQueue() { return new Queue(REVISION_VALIDATION_QUEUE, true, false, false); }
    @Bean Binding revisionValidationBinding(Queue revisionValidationQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(revisionValidationQueue).to(jobExchange).with("REVISION_VALIDATION_QUEUED");
    }
    @Bean Queue revisionPublishQueue() { return new Queue(REVISION_PUBLISH_QUEUE, true, false, false); }
    @Bean Binding revisionPublishBinding(Queue revisionPublishQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(revisionPublishQueue).to(jobExchange).with("REVISION_PUBLISH_QUEUED");
    }
    @Bean Declarables deletedQueues(DirectExchange jobExchange) {
        List<Declarable> declarables = new ArrayList<>();
        for (String stage : DELETED_STAGES) {
            Queue queue = new Queue("pdf.gateway.deleted." + stage + ".v1", true, false, false);
            declarables.add(queue);
            declarables.add(BindingBuilder.bind(queue).to(jobExchange).with("DOCUMENT_DELETED"));
        }
        return new Declarables(declarables);
    }
}
