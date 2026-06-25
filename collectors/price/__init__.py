"""collectors.price -- canonical daily ETF price citizen (INIT-22 P3).

ONE yfinance pull/day for the ETF universe (131 ETF-rr + idx_dxy = 132) -> every bar
written through the P1 archive primitive (datacore.archive) into the SEPARATE
price-archive store (P2): append-only, year-partitioned, bitemporal. ETF only;
stocks are P7+. Backfill to inception is P4. P3 writes a TEMP archive root only.
"""
