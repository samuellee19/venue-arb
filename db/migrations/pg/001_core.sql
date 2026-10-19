-- Pair convention: a = Kalshi, b = Polymarket. No CREATE EXTENSION: vector is already installed in `app`.
-- schema venue_arb already exists, owned by role venue_arb (bin/project-db.sh); never created here

CREATE TABLE venue_arb.listings (
    venue text NOT NULL,
    market_id text NOT NULL,
    event_id text,
    category text,
    title text,
    rules text,
    yes_label text,
    no_label text,
    yes_token text,
    no_token text,
    close_time timestamptz,
    resolution_source text,
    version text,
    raw jsonb,
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (venue, market_id)
);

CREATE TABLE venue_arb.embeddings (
    venue text NOT NULL,
    market_id text NOT NULL,
    version text NOT NULL,
    vec vector(384) NOT NULL,
    PRIMARY KEY (venue, market_id, version)
);

CREATE TABLE venue_arb.candidates (
    a_venue text NOT NULL,
    a_id text NOT NULL,
    b_venue text NOT NULL,
    b_id text NOT NULL,
    source text NOT NULL CHECK (source IN ('rule', 'embed')),
    score real,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (a_venue, a_id, b_venue, b_id)
);

CREATE TABLE venue_arb.adjudications (
    a_venue text NOT NULL,
    a_id text NOT NULL,
    a_version text NOT NULL,
    b_venue text NOT NULL,
    b_id text NOT NULL,
    b_version text NOT NULL,
    model text,
    relation text CHECK (relation IN ('exact', 'near', 'different')),
    yes_maps_to text CHECK (yes_maps_to IN ('yes', 'no')),
    deadline_diff text,
    threshold_diff text,
    resolution_diff text,
    reason text,
    confidence real,
    cost_usd numeric(10, 6),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (a_venue, a_id, a_version, b_venue, b_id, b_version)
);

CREATE TABLE venue_arb.reviews (
    a_venue text NOT NULL,
    a_id text NOT NULL,
    a_version text NOT NULL,
    b_venue text NOT NULL,
    b_id text NOT NULL,
    b_version text NOT NULL,
    label text NOT NULL CHECK (label IN ('correct', 'wrong', 'unsure')),
    note text,
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (a_venue, a_id, a_version, b_venue, b_id, b_version)
);
