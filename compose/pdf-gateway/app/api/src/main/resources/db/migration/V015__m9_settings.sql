-- Choices an admin makes on the page that the gateway's parts follow (the AI model).
CREATE TABLE gateway_settings (
    key varchar(40) PRIMARY KEY,
    value text NOT NULL,
    updated_by uuid REFERENCES users(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
