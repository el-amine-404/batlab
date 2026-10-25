package net.batlab.pdfgateway.queue;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
class RabbitConfig {
    static final String EXCHANGE = "pdf.gateway.jobs";
    static final String QUEUE = "pdf.gateway.scan.v1";
    static final String SIGNATURE_QUEUE = "pdf.gateway.signature.v1";
    @Bean DirectExchange jobExchange() { return new DirectExchange(EXCHANGE, true, false); }
    @Bean Queue scanQueue() { return new Queue(QUEUE, true, false, false); }
    @Bean Binding scanBinding(Queue scanQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(scanQueue).to(jobExchange).with("SCAN_QUEUED");
    }
    @Bean Queue signatureQueue() { return new Queue(SIGNATURE_QUEUE, true, false, false); }
    @Bean Binding signatureBinding(Queue signatureQueue, DirectExchange jobExchange) {
        return BindingBuilder.bind(signatureQueue).to(jobExchange).with("SIGNATURE_QUEUED");
    }
}
