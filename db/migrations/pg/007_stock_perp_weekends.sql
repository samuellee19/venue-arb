-- Weekend-tier reference prices per perp symbol and Friday: the Friday, Saturday and Sunday perp marks and the
-- pre-open and regular-open prices, with the reference kind used. One row per (symbol, friday).
CREATE TABLE IF NOT EXISTS venue_arb.stock_perp_weekends (
    symbol text,
    friday date,
    p_fri double precision,
    p_sat double precision,
    p_sun double precision,
    p_open_pre double precision,
    p_open_reg double precision,
    ref_kind text NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, friday)
);
