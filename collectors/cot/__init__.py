"""COT positioning collector — second citizen of data-core (INIT-22 S13).

Migrates the cot-monitor / cot-cta dashboards into a data-core citizen: raw
weekly COT spec-net positions land in the guarded base (identity + schema +
health), one series per market. Percentiles are NOT baked here — they are a
parametrized view derived on the clean segment by consumers (kills the audit's
spliced-window bug). The dashboards become consumers that point at the base.
"""
