package net.batlab.pdfgateway.queue;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
class OutboxPublisher {
    record Event(UUID id, String type, String payload) {}
    private final JdbcTemplate jdbc;
    private final RabbitTemplate rabbit;
    OutboxPublisher(JdbcTemplate jdbc, RabbitTemplate rabbit) { this.jdbc = jdbc; this.rabbit = rabbit; }

    @Scheduled(fixedDelayString = "${PDF_GATEWAY_OUTBOX_DELAY_MS:1000}")
    void publish() {
        List<Event> events = jdbc.query("SELECT id,event_type,payload::text FROM outbox_events WHERE published_at IS NULL AND available_at<=now() ORDER BY created_at LIMIT 20",
                (rs, row) -> new Event(rs.getObject(1, UUID.class), rs.getString(2), rs.getString(3)));
        for (Event event : events) {
            try {
                CorrelationData confirmation = new CorrelationData(event.id().toString());
                rabbit.convertAndSend(RabbitConfig.EXCHANGE, event.type(), event.payload(), message -> {
                    message.getMessageProperties().setMessageId(event.id().toString());
                    message.getMessageProperties().setContentType("application/json");
                    message.getMessageProperties().setDeliveryMode(org.springframework.amqp.core.MessageDeliveryMode.PERSISTENT);
                    return message;
                }, confirmation);
                CorrelationData.Confirm result = confirmation.getFuture().get(10, TimeUnit.SECONDS);
                if (!result.ack()) throw new IllegalStateException("broker rejected message: " + result.reason());
                if (confirmation.getReturned() != null) throw new IllegalStateException("broker returned unroutable message");
                jdbc.update("UPDATE outbox_events SET published_at=?,attempts=attempts+1,last_error=NULL WHERE id=? AND published_at IS NULL", OffsetDateTime.now(), event.id());
            } catch (Exception error) {
                String message = error.getClass().getSimpleName() + ": " + String.valueOf(error.getMessage());
                jdbc.update("UPDATE outbox_events SET attempts=attempts+1,last_error=?,available_at=now()+interval '30 seconds' WHERE id=?", message.substring(0, Math.min(500, message.length())), event.id());
            }
        }
    }
}
