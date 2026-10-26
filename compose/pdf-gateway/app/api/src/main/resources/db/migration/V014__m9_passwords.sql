-- People sign in on the page with a name and a password; API tokens stay for
-- apps and scripts (an account without a password signs in with nothing else).
-- The password is a salted PBKDF2 hash, never the password.
ALTER TABLE users ADD COLUMN password_hash varchar(200);
ALTER TABLE users ADD COLUMN password_changed_at timestamptz;
ALTER TABLE users ADD COLUMN last_login_at timestamptz;

-- The setup code, while no admin can sign in with a password.
CREATE TABLE account_state (
    key varchar(40) PRIMARY KEY,
    value varchar(200) NOT NULL
);
