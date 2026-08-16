# Commodity Verdict source contract

## Source

- Provider: Multi Commodity Exchange of India (MCX)
- Public page: `https://www.mcxindia.com/market-data/bhavcopy`
- Machine endpoint: `https://www.mcxindia.com/backpage.aspx/GetDateWiseBhavCopy`
- Method: `POST`
- Request content type: `application/json; charset=UTF-8`
- Request body: `{"Date":"YYYYMMDD","InstrumentName":"FUTCOM"}`
- Scheduled acquisition: Monday-Friday at 08:45 IST (03:15 UTC), searching the preceding eight calendar days and selecting the first non-empty Bhav Copy.

## Exact commodity identity

Only these exchange symbols are accepted: `GOLD`, `SILVER`, `CRUDEOIL`, `ZINC`, `COPPER`. `GOLDM`, `SILVERM`, `CRUDEOILM`, `ZINCMINI`, `COPPERM`, or any other related symbol cannot substitute.

For each exact symbol, the adapter selects the nearest unexpired `FUTCOM` contract that has positive volume and internally valid OHLC values. If none exists, that commodity is omitted from the adapter result and the verdict engine emits `NOT_RECOMMEND` for it.

## Source fields

| Source semantic | Accepted raw keys | Production meaning |
|---|---|---|
| Instrument | `InstrumentName`, `Instrument`, `InstrumentType` | Must be `FUTCOM` |
| Commodity | `Symbol`, `Commodity`, `CommodityName`, `Product` | Exact MCX commodity identity |
| Expiry | `ExpiryDate`, `Expiry` | Contract expiry used for deterministic contract selection |
| Open | `Open`, `OpenPrice` | EOD opening price |
| High | `High`, `HighPrice` | EOD high |
| Low | `Low`, `LowPrice` | EOD low |
| Close / LTP reference | `Close`, `ClosingPrice`, `ClosePrice`, `SettlementPrice` | EOD close; this is explicitly an EOD reference, not an 08:45 live trade |
| Previous close | `PCP`, `PrevClose`, `PreviousClose`, `PreviousClosePrice` | Directional bias input |
| Volume | `Volume`, `Vol`, `VolumeLots`, `TotalQuantityTraded` | Contract-selection/liquidity evidence |
| Open interest | `OI`, `OpenInterest`, `OiQty` | Audit-only evidence in v1.0 |

## Derived fields

The source does not publish our strategy-specific breakout/invalidation fields. They are derived deterministically from the selected EOD bar using classic pivot levels:

- `P = (High + Low + Close) / 3`
- breakout level = `R1 = 2P - Low`
- breakdown level = `S1 = 2P - High`
- BUY invalidation below = `P`
- SELL invalidation above = `P`
- trend bias = `BUY` when `Close > PCP`, `SELL` when `Close < PCP`, otherwise `NEUTRAL`
- ATR = unavailable from a one-day Bhav Copy and therefore remains null rather than being fabricated

The exact MCX response field names observed by a production run are written to `adapter.diagnostics.json` under `response_fields`. The selected real contracts and their OHLC/volume/OI are written under `selected_contracts`, making each scheduled run auditable without embedding sample trade calls in the frontend.

## Freshness and fail-closed behavior

At 08:45 IST the normal MCX session has not opened. The adapter therefore intentionally begins with the previous calendar date and searches backward to handle weekends and exchange holidays. It does not describe the EOD close as a fresh live LTP. A source older than the configured seven-day freshness bound, missing exact commodity, malformed OHLC, invalid geometry, or failed verification produces `NOT_RECOMMEND` through the verdict engine.

## Licensing

MCX describes Bhav Copy as exchange end-of-day data and states that data-feed usage and redistribution are governed by its agreements. This repository therefore records MCX as the source but does not claim that public web availability grants unrestricted commercial redistribution rights. Production/commercial reuse should be checked against the applicable MCX terms or data-feed agreement.
