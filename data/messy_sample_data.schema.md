# Data fixture specification: `messy_sample_data.csv`

This document describes the exact frozen fixture in [messy_sample_data.csv](messy_sample_data.csv). Line numbers are one-based physical lines as displayed by an editor. Product behavior remains governed by [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); this document inventories the fixture and applies those rules to its observed values.

## 1. Frozen fixture identity

| Property | Value |
|---|---|
| SHA-256 | `e550ba421ebaac800d2e734c513f65d2bc4197f8b622a2fc82230974744110c4` |
| Bytes | 5,953 |
| Physical lines | 54, with a trailing newline |
| Logical CSV records | 52 |
| Headers | 2: initial line 1 and repeated line 19 |
| Blank records | 2: lines 18 and 45 |
| Data records | 48: 47 distinct plus one exact duplicate |
| Entity types | `CUSTOMER` ×16, `PRODUCT` ×16, `ORDER` ×16 |
| Logical model | Discriminated union keyed by `record_type` |

The fixture is a denormalised export in which three entity types share ten columns. The `name`, `contact_or_sku`, `value`, `quantity`, `date`, and `status` columns have type-specific meanings.

## 2. Physical CSV format

| Aspect | Intended form | Observed form |
|---|---|---|
| Encoding | UTF-8 | Starts with UTF-8 BOM `EF BB BF`; includes Latin, CJK, Arabic, emoji, and other non-ASCII text. |
| Line ending | LF | No CR characters. |
| Delimiter | `,` | Commas inside valid fields are quoted; line 14 contains unquoted commas. |
| Quote | `"` | RFC 4180 doubled quotes occur on line 7. |
| Header | One 10-field header | Exact header repeats on line 19 after blank line 18. |
| Data width | 10 fields | Lines 13, 14, 24, 31, and 39 have non-ten-field shapes. |
| Multiline fields | Quoted newlines allowed | Product notes span lines 15–16 and 36–37. |
| Blank lines | Preserved evidence | Lines 18 and 45 parse as empty logical records. |

Simplified grammar:

```abnf
file        = [BOM] *(header / record / blank-line)
header      = "record_type,id,name,contact_or_sku,value,quantity,date,status,tags,notes"
record      = field 9(COMMA field)       ; intended width; malformed rows are retained
field       = escaped / non-escaped
escaped     = DQUOTE *(TEXTDATA / COMMA / LF / 2DQUOTE) DQUOTE
non-escaped = *TEXTDATA
BOM         = %xEF.BB.BF
blank-line  = LF
```

Parse stores every logical item and its inclusive physical-line span. It does not silently pad, merge, discard, or rewrite raw fields.

## 3. Shared columns

| # | Column | Meaning |
|---|---|---|
| 1 | `record_type` | `CUSTOMER`, `PRODUCT`, or `ORDER` discriminator |
| 2 | `id` | Customer ID, product SKU, or order ID |
| 3 | `name` | Customer name, product name, or order customer name |
| 4 | `contact_or_sku` | Customer email, product category, or order product SKU |
| 5 | `value` | Lifetime spend, product unit price, or order unit price |
| 6 | `quantity` | Unused for customers, product stock, or order quantity |
| 7 | `date` | Signup date, listed date, or order timestamp |
| 8 | `status` | Type-specific status |
| 9 | `tags` | Pipe-delimited labels |
| 10 | `notes` | Free text |

## 4. Intended logical types

### 4.1 Customer

| Source field | Candidate field | Type and rule | Observed complications |
|---|---|---|---|
| `id` | `customer_id` | String matching `^CUST-\d{4}$` | All IDs match, including `CUST-0012`. |
| `name` | `name` | Non-empty trimmed Unicode string | Apostrophe, accents, CJK, Arabic, emoji, padded text, comma, hyphen, and all-uppercase placeholder occur. |
| `contact_or_sku` | `email` | One email or absent | `-`, `n/a`, and two comma-separated emails occur. |
| `value` | `lifetime_spend` | Non-negative decimal plus source currency | Currency symbols/codes, localized numbers, annotations, a negative amount, and conversion prose occur. |
| `quantity` | — | Must be empty | Line 39 is structurally shifted, so emptiness cannot be established safely. |
| `date` | `signup_date` | Date | ISO, US slash, named month, datetime, dotted forms, slash form, and `unknown` occur. |
| `status` | `status` | `active` or `inactive` | Case variants and approved `Y→active` occur; `true`, `PENDING`, and `unknown` are unsupported. |
| `tags` | `tags` | Ordered lowercase list | Empty, `N/A`, malformed delimiters, and new vocabulary occur. |
| `notes` | `notes` | Trimmed Unicode text or absent | Contains referral text and free-form evidence. |

Lifetime spend is not derived from orders in this file and must not be reconciled against order totals.

### 4.2 Product

| Source field | Candidate field | Type and rule | Observed complications |
|---|---|---|---|
| `id` | `sku` | String matching `^SKU-\d{4}$` | `SKU-00204` and `SKU-00209` have five-digit zero-padded bodies. |
| `name` | `name` | Non-empty Unicode string | Hyphens, parentheses, and quoted commas occur. |
| `contact_or_sku` | `category` | Registered category | Approved values coexist with `Storage`, `Office`, `Office, Electronics`. |
| `value` | `unit_price` | Positive decimal plus currency | Deferred, decimal comma, annotation, discount prose, dual values, arithmetic expression, and dual currencies occur. |
| `quantity` | `stock_qty` | Integer; negative means backorder | `many` and `5|in-stock` are invalid integers; negative values occur. |
| `date` | `listed_date` | Date or absent | ISO dates plus a datetime occur. |
| `status` | `status` | Registered product status | Missing, `out_of_stock`, and `pre_order` are outside the approved enum. |
| `tags` | `tags` | Ordered lowercase list | Several new tags occur and require vocabulary validation. |
| `notes` | `notes` | Trimmed Unicode text or absent | Two multiline notes and one status contradiction occur. |

Approved status/stock invariants are:

- `backordered` requires `stock_qty < 0`;
- `discontinued` requires `stock_qty = 0`;
- `in_stock` requires `stock_qty > 0`;
- `pending_review` permits unresolved price, stock, and date.

### 4.3 Order

| Source field | Candidate field | Type and rule | Observed complications |
|---|---|---|---|
| `id` | `order_id` | String matching `^ORD-\d{4}$`, unique per identity | `ORD-3001` is repeated exactly on lines 4 and 17. |
| `name` | `customer_name_raw` | Non-empty string resolved through a match key | Emoji, punctuation, capitalization, transliteration, and spelling differences affect matching. |
| `contact_or_sku` | `sku` | Reference to a product SKU | Two zero-padded SKUs require governed repair before resolution. |
| `value` | `unit_price` | Positive decimal plus currency | Null, zero, foreign currency, and one line-total value occur. |
| `quantity` | `quantity` | Non-zero integer; negative means refund | `None` and absent occur. |
| `date` | `ordered_at` | Date or datetime | ISO, slash, dotted, two-digit-year, fractional-second, offset-like, and missing forms occur. |
| `status` | `status` | `pending`, `shipped`, `cancelled`, or `refunded` | Missing and six unsupported states occur. |
| `tags` | `tags` | Ordered lowercase list | `express` plus new vocabulary occurs. |
| `notes` | `notes` | Trimmed Unicode text or absent | Escaped quotes and extra trailing empty fields occur. |

Order `value` is always a unit price. `ORD-3003` contains a line total: `$39.98 ÷ 2 = $19.99`; the registered rule appends a repaired revision before FX conversion.

The approved refund invariant is `quantity < 0` if and only if `status = refunded`.

## 5. Approved value rules

### 5.1 Field states and null tokens

| Input | Approved interpretation |
|---|---|
| Empty field | `absent` |
| `-` in customer email | `absent` |
| `N/A` in tags | empty list |
| `NULL` in order value | `absent` |
| `None` in order quantity | `absent` |
| Missing trailing column | `absent`, with structural issue when required |
| `TBD` in product price | `deferred` |
| Unparseable text such as `many` | `unresolved`, with typed issue |

Tokens are field-sensitive. Lowercase `n/a` in customer email, `na` in tags, and `unknown` are not silently treated as approved null tokens.

### 5.2 Money

Approved deterministic cases:

| Raw | Amount | Currency | Additional data |
|---|---:|---|---|
| `19.99` | 19.99 | GBP | — |
| `$1,240.50` | 1240.50 | USD | — |
| `$39.98` | 39.98 | USD | repaired to unit price before FX for `ORD-3003` |
| `45,00` | 45.00 | GBP | — |
| `€2.345,00` | 2345.00 | EUR | — |
| `1.5e3` | 1500.00 | GBP | — |
| ` 275.00 ` | 275.00 | GBP | trimming provenance |
| `12.99 (10% off)` | 12.99 | GBP | annotation `10% off` |
| `£759.99 (GBP)` | 759.99 | GBP | annotation `GBP` |

The algorithm trims, extracts a trailing parenthetical annotation, identifies a leading `$`, `€`, or `£`, handles scientific notation, interprets the rightmost of `.` and `,` as the decimal mark, treats a single comma followed by two digits as decimal, and otherwise treats a comma as thousands. Calculations use decimal arithmetic and round to two places.

The following observed forms are outside that grammar and remain unresolved for review: `149.99|with discount code`, `USD 1200.50`, `85.50|EUR 79.90`, `$8.99 x5=$44.95`, `3,500 SR`, `¥15000`, `$69.99 | €65.00`, and `$2,340 MXN converted @ 17.5 = $133.71`.

Foreign amounts preserve source amount and currency. Orders use the latest eligible ECB rate on or before the order date; lifetime spend uses the run snapshot. Missing rates create `FX_RATE_UNAVAILABLE` and prevent staging.

### 5.3 Dates

Approved deterministic inputs:

| Raw form | Interpretation |
|---|---|
| `YYYY-MM-DD` | Date |
| `MM/DD/YYYY` | US month/day/year date |
| `DD-Mon-YYYY` | Named-month date |
| `YYYY/MM/DD HH:MM:SS` | Local datetime with no timezone |
| Padded approved form | Trim before parsing |
| Empty field | Absent |

Slash dates with four-digit years are month/day/year, so `03/04/2023` means 4 March 2023.

Observed forms outside the approved registry remain unresolved: `2024-01-10T15:30:00Z`, `3/15/24`, `01-02-2024`, `2024-02-20 09:15`, `2024.04.10`, `15.3.2023`, `2024-05-22 14:30:45.123`, `2023/12/15`, `2024-07-01 +00:00`, `2023.11.20`, `2024-08-15T10:00:00`, `unknown`, `2024-09-01 13:45`, and missing required order dates. They may become supported only through an explicit registered rule and corresponding unit test.

### 5.4 Statuses

- Customer mappings: `Active`, `ACTIVE`, and `active` → `active`; `Inactive` and `inactive` → `inactive`; `Y` → `active`.
- Product approved values: `in_stock`, `discontinued`, `pending_review`, `backordered`.
- Order approved values: `pending`, `shipped`, `cancelled`, `refunded`.

Unsupported values remain unresolved: customer `true`, `PENDING`, `unknown`; product empty, `out_of_stock`, `pre_order`; order empty, `in_transit`, `processing`, `backorder`, `returned`, `awaiting_shipment`, `completed`.

### 5.5 Tags and free text

Tags use `^[a-z]+(\|[a-z]+)*$`, are split on `|`, lowercased, and retain order. Empty and approved `N/A` become an empty list. Leading/trailing separators such as `|vip|` are malformed and require review.

Free text is trimmed only at its outer edges. Internal punctuation, commas, apostrophes, ampersands, accents, emoji, CJK and Arabic characters, and newlines are preserved.

Matching keys are separate derived values: Unicode NFKC, removal of symbol-category characters such as emoji, whitespace collapse, then case-folding. The source/display value is never replaced by its match key. This resolves `Sofia Rossi` to `Sofia Rossi 🌟` and `Wei Zhang` to `  Wei Zhang  `. It does not justify transliteration or punctuation removal that the registered algorithm does not perform.

## 6. Complete structural exception catalogue

| Physical line(s) | Record | Raw shape | Required treatment |
|---|---|---|---|
| 1 | Header | UTF-8 BOM | Recognise header using UTF-8-with-signature decoding; retain original bytes. |
| 13 | `ORD-3004` | 7 fields | Preserve seven raw fields; candidate marks missing trailing status, tags, and notes absent and creates missing-status review. |
| 14 | `CUST-1005` | 12 fields from unquoted note commas | Preserve all fields; approved last-column salvage joins fields 10–12 and records a lossy structural-repair issue. |
| 15–16 | `SKU-2005` | Quoted multiline note | Store one raw record spanning both lines; preserve newline. |
| 17 | `ORD-3001` | Exact duplicate of line 4 | Preserve both; later occurrence classifies `DUPLICATE`. |
| 18 | — | Blank record | Preserve as `blank`. |
| 19 | Header | Exact repeated header | Preserve as `repeated_header`; never classify as data. |
| 24 | `SKU-2007` | 11 fields; extra trailing empty | Preserve 11 raw fields; deterministic trailing-empty salvage may create the ten-field candidate with provenance. |
| 31 | `ORD-3008` | 11 fields; extra trailing empty | Preserve 11 raw fields; deterministic trailing-empty salvage may create the ten-field candidate with provenance. |
| 36–37 | `SKU-2011` | Quoted multiline note | Store one raw record spanning both lines; preserve newline. |
| 39 | `CUST-0012` | 9 fields with an apparent mid-row omission and shifted values | Preserve nine fields. Do not guess the missing column or realign values; create a rejected shell and structural-review item. |
| 45 | — | Blank record | Preserve as `blank`. |

All other data records contain ten parsed fields.

## 7. Semantic exception catalogue

### 7.1 Original segment, lines 2–25

| Line | Record | Field(s) | Observation and governed outcome |
|---:|---|---|---|
| 2 | `CUST-1001` | value | USD with thousands separator; convert at run snapshot and record FX provenance. |
| 5 | `CUST-1002` | date, status, tags, notes | US date; status case mapping; `N/A` tags; referral to `CUST-1001` in text. |
| 6 | `SKU-2002` | value | Decimal comma parses to GBP 45.00. |
| 7 | `ORD-3002` | quantity, notes | Negative refund quantity; escaped quotes are normal CSV mechanics. |
| 8 | `CUST-1003` | value, date, status | EUR localized amount; named-month date; inactive customer has later order, which is review evidence rather than an automatic contradiction repair. |
| 9 | `SKU-2003` | value, quantity, date | Deferred price, unresolved integer, absent date; requires review. |
| 10 | `ORD-3003` | value, date | USD line total repaired to unit price 19.99 before FX; supported slash datetime. |
| 11 | `CUST-1004` | contact, value | Absent email placeholder; scientific-notation GBP value. |
| 12–13 | `SKU-00204`, `ORD-3004` | SKU | Registered zero-padding repair maps both references to `SKU-2004` through appended revisions. |
| 20 | `CUST-1006` | text, value, date, status | Outer whitespace trims; `Y` maps to active. |
| 21 | `SKU-2006` | value | Price annotation is retained as metadata. |
| 22 | `ORD-3005` | value, quantity | Approved null tokens on a cancelled order. |
| 23, 25 | `CUST-1007`, `ORD-3006` | name | Match key relates name with and without emoji while preserving both values. |
| 25 | `ORD-3006` | status/notes | Pending order versus in-stock product is retained as a reviewable minor contradiction. |

### 7.2 Expanded segment, lines 26–54

| Line | Record | Field(s) | Observation and governed outcome |
|---:|---|---|---|
| 26 | `CUST-1008` | date, status, tags | ISO timestamp where a date is expected, unsupported `true`, and new `premium` tag; review. Umlaut is preserved. |
| 27 | `SKU-2008` | value, status, tags | Discount prose in price, missing status, and new tags; price/status unresolved and review required. |
| 28 | `ORD-3007` | date, status, tags, notes | Two-digit-year date, unsupported `in_transit`, new `overnight` tag, and line-total prose in notes; review. |
| 29 | `CUST-1009` | value, date | Currency-code prefix and ambiguous hyphen date are unsupported; preserve CJK text and review. |
| 30 | `SKU-00209` | id, category, value, date, notes | Zero-padded ID, new category, dual price, datetime in date field, and notes contradicting `in_stock`; no repair beyond a registered rule, so review. |
| 31 | `ORD-3008` | sku, date, status, width | Depends on unresolved `SKU-00209`; dotted date and `processing` are unsupported; trailing empty field is structurally salvageable. |
| 32 | `CUST-1010` | email, date | Two emails in one field and dotted date require review; accented/hyphenated name is preserved. |
| 33 | `SKU-2010` | category, status | `Office` and `out_of_stock` are outside registered domains; stock is zero. Review without inventing a mapping. |
| 34 | `ORD-3009` | value, date, status, tags | Zero unit price violates positivity; fractional-second datetime, `backorder`, and new tag are unsupported. |
| 35 | `CUST-1011` | name, value, date, tags | Quoted comma in name is valid; GBP annotation is parseable; slash date and `na` tag are unsupported. |
| 36–37 | `SKU-2011` | category, quantity, notes | Multi-category string and `5|in-stock` integer are unresolved; multiline note is valid CSV. |
| 38 | `ORD-3010` | customer, date | Punctuation difference prevents the approved match-key algorithm from resolving `Johnson Michael` to `Johnson, Michael`; offset-like date is unsupported. |
| 39 | `CUST-0012` | entire row | Mid-row omission shifts subsequent values. Reject the candidate shell for structural review; do not infer columns. |
| 40 | `SKU-2012` | value, quantity/status, tags | Arithmetic price expression unresolved; `-8` conflicts with `discontinued`, which requires zero; new tag. |
| 41 | `ORD-3011` | status, tags | Four-digit US slash date is supported; `returned` and `priority` are unsupported. |
| 42 | `CUST-1013` | value, date | Saudi-riyal suffix format and dotted date are unsupported; Arabic name is preserved. |
| 43 | `SKU-2013` | name, tags | Quoted commas are valid; new tags require vocabulary validation. |
| 44 | `ORD-3012` | customer, date, tags | Transliteration cannot resolve to the Arabic customer through the approved match key; ISO datetime and new tag still require registered support/review. |
| 46 | `CUST-1014` | value | Yen-symbol amount is outside the approved money parser and requires review; Japanese text is preserved. |
| 47 | `SKU-2014` | category, value | New category and two alternative currency prices in one field are unresolved. |
| 48 | `ORD-3013` | date, status | Slash datetime is supported; `awaiting_shipment` is not. |
| 49 | `CUST-1015` | name, value, date, status | Placeholder-like name, negative lifetime spend, and `unknown` date/status require review. |
| 50 | `SKU-2015` | status/stock | `pre_order` is unsupported; zero stock is evidence but does not authorize a mapping. |
| 51 | `ORD-3014` | date, status | Required date and status are absent; customer name can match case-insensitively. Review. |
| 52 | `CUST-1016` | value | Narrative MXN conversion cannot be parsed as one governed amount; preserve raw evidence and review. |
| 53 | `SKU-2016` | category, tags | `Office` and `desk` are outside the current registered vocabularies; numeric/date/status fields otherwise parse. |
| 54 | `ORD-3015` | date, status, tags | Space-separated minute datetime, `completed`, and `standard` lack registered mappings; review. |

## 8. Relationship and duplicate expectations

```text
CUSTOMER (identity) 1 ──< ORDER (customer reference; legacy name-match fallback)
PRODUCT  (identity) 1 ──< ORDER (SKU reference)
CUSTOMER (identity) 1 ──< CUSTOMER (referral extracted from notes)
```

- Exact duplication compares parsed raw field arrays within a run before normalisation and ignores source-line numbers.
- Lines 4 and 17 are the only exact duplicate pair in this fixture.
- Name matching is lossy and never overwrites raw names.
- Unresolved product or customer relations block order readiness separately from the order's field verdict.
- A repeated business ID with different fields is a conflict, not a duplicate.

## 9. Golden-test expectations

The golden integration test must verify:

- the hash, byte count, physical-line count, logical-record count, and entity counts in section 1;
- exact source spans for every raw record, including both multiline records;
- the five non-ten-field rows and their exact field counts;
- preservation of both headers, both blanks, and both duplicate occurrences;
- one initial candidate or rejected shell for every data record;
- one terminal classification per data record for the active rules version;
- traceability from each staged or reviewed result to frozen bytes, raw fields, candidate revisions, transformations, issues, and classification;
- idempotent equality of the final persisted graph after uninterrupted execution and after failure/retry at every batch boundary.

Any change to the CSV requires an intentional update to its SHA-256, structural inventory, semantic catalogue, and golden-test expectations in the same change.
