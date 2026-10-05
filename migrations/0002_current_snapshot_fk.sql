ALTER TABLE sessions
    ADD CONSTRAINT sessions_current_snapshot_fk
    FOREIGN KEY (session_id, current_revision)
    REFERENCES campaign_snapshots (session_id, revision)
    DEFERRABLE INITIALLY DEFERRED;
