ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS campaign_type TEXT NOT NULL DEFAULT 'dragon-delves'
        CHECK (campaign_type IN ('dragon-delves', 'dynamic'));

ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS campaign_title TEXT;
