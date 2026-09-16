# Data Structure Specification: `messy_sample_data.csv`

This document formally describes the structure of [messy_sample_data.csv](messy_sample_data.csv):
the intended schema, the variants actually observed, and every exception and edge case found.
Line numbers refer to **physical lines** in the file (1-based), as shown in an editor.

---

## 1. Summary

| Property | Value |
|---|---|
| Physical lines | 25 (+ trailing newline) |
| Parsed CSV records | 24, of which 2 are headers and 1 is blank |
| Data records | 21 (20 distinct: one exact duplicate) |
| Entity types | `CUSTOMER` ×7, `PRODUCT` ×7, `ORDER` ×7 (6 distinct) |
| Logical model | **Discriminated union** (a single table with three entity types), keyed on `record_type` |

The file is a denormalised export in which three unrelated entity types share one set of ten
columns. Several columns mean different things depending on `record_type` (see §4).

---

## 2. File-level format (physical layer)

| Aspect | Specification | Observed / Exceptions |
|---|---|---|
| Encoding | UTF-8 | File starts with a **UTF-8 BOM** (`EF BB BF`). Naive readers will name the first column `﻿record_type`. |
| Line terminator | LF (`\n`) | No CR characters present. |
| Delimiter | `,` | — |
| Quote char | `"` | RFC 4180 style; embedded `"` escaped as `""` (line 7). |
| Header | One header row, 10 columns | Header **repeats** on line 19, after a blank line (18). This looks like two files concatenated. |
| Field count | Exactly 10 per record | Violated on lines 13 (7), 14 (12) and 24 (11). See §6.1. |
| Multi-line fields | Allowed inside quotes | Line 15–16: one record spans two physical lines. |
| Non-BMP characters | Allowed | `🌟` (U+1F31F) on line 23. It breaks storage that only handles 3-byte UTF-8, such as MySQL `utf8`/`utf8mb3`. |

### 2.1 Grammar (ABNF, simplified from RFC 4180)

```abnf
file        = [BOM] section *(blank-line section) [LF]
section     = header LF record *(LF record)
header      = "record_type,id,name,contact_or_sku,value,quantity,date,status,tags,notes"
record      = field 9(COMMA field)            ; intended: exactly 10 fields
field       = escaped / non-escaped
escaped     = DQUOTE *(TEXTDATA / COMMA / LF / 2DQUOTE) DQUOTE
non-escaped = *TEXTDATA
BOM         = %xEF.BB.BF
blank-line  = LF
```

---

## 3. Columns

| # | Column | Role |
|---|---|---|
| 1 | `record_type` | Discriminator: `CUSTOMER` \| `PRODUCT` \| `ORDER` |
| 2 | `id` | Primary key; its prefix matches the type |
| 3 | `name` | Customer name / product name / *customer name the order belongs to* |
| 4 | `contact_or_sku` | **Overloaded**: email / category / product SKU (foreign key) |
| 5 | `value` | **Overloaded**: lifetime spend / unit price / unit price |
| 6 | `quantity` | **Overloaded**: unused / stock level / ordered quantity |
| 7 | `date` | **Overloaded**: signup date / listing date / order date |
| 8 | `status` | Enum; the allowed values depend on `record_type` |
| 9 | `tags` | Pipe-delimited list of labels |
| 10 | `notes` | Free text |

---

## 4. Logical schema per record type

Canonical types are the types each field should have **after normalisation**.
"Observed" lists the raw forms that actually occur.

### 4.1 `CUSTOMER`

| Field | Canonical type | Constraint | Observed variants |
|---|---|---|---|
| `id` | string | `^CUST-\d{4}$`, unique | All conform (`CUST-1001` … `CUST-1007`) |
| `name` | string, non-empty | trimmed | `Mary O'Brien` (apostrophe), `François Dupont` (non-ASCII), `  Wei Zhang  ` (padding), `Sofia Rossi 🌟` (emoji) |
| `contact_or_sku` → **`email`** | string \| null | RFC 5322-ish email | `-` used as a null placeholder (CUST-1004) |
| `value` → **`lifetime_spend`** | decimal(12,2) \| null | ≥ 0; GBP | `$1,240.50`, `890.00`, `€2.345,00`, `1.5e3`, ` 275.00 `, `410.00`, `1050.25` |
| `quantity` | — (always null) | must be empty | Always empty ✔ |
| `date` → **`signup_date`** | date | ISO 8601 | `2023-05-12`, `01/22/2023`, `15-Jan-2024`, ` 2023-08-01 ` |
| `status` | enum `{active, inactive}` | case-insensitive | `Active`, `ACTIVE`, `active`, `inactive`, `Y` |
| `tags` | list\<string\> | vocabulary: `vip`, `newsletter`, `loyalty` | `vip\|newsletter`, `N/A`, empty |
| `notes` | string \| null | — | Contains an implicit foreign key: "referral from **CUST-1001**" (line 5) |

Note: `lifetime_spend` is confirmed lifetime spend in GBP. It cannot be derived from the orders in this file
(for example, John Smith is 1,240.50 but has only a 19.99 order) because it includes history outside
this extract. Do not validate it against order sums.

### 4.2 `PRODUCT`

| Field | Canonical type | Constraint | Observed variants |
|---|---|---|---|
| `id` → **`sku`** | string | `^SKU-\d{4}$`, unique | `SKU-00204` (5 digits, zero-padded; typo, normalised to `SKU-2004`) |
| `name` | string, non-empty | — | `Bluetooth Keyboard - DE Layout` (contains ` - `) |
| `contact_or_sku` → **`category`** | enum-like string | `{Electronics, Home & Office, Accessories}` | `&` in value |
| `value` → **`unit_price`** | decimal(10,2) \| null | > 0 | `19.99`, `45,00` (decimal comma), `TBD`, `12.99 (10% off)` (annotated) |
| `quantity` → **`stock_qty`** | integer \| null | may be negative (= units on backorder) | `150`, `0`, `-12`, `many` |
| `date` → **`listed_date`** | date \| null | ISO 8601 | Empty on SKU-2003 |
| `status` | enum `{in_stock, discontinued, pending_review, backordered}` | lowercase snake_case | All conform |
| `tags` | list\<string\> | vocabulary: `electronics`, `accessories`, `limited`, `new`, `home`, `office`, `ergonomic` | Some overlap with `category` |
| `notes` | string \| null | — | Embedded newline (SKU-2005); stray extra field (SKU-2007) |

**Expected status/quantity invariants** (all hold in the sample):

- `backordered` ⇒ `stock_qty < 0`
- `discontinued` ⇒ `stock_qty = 0`
- `in_stock` ⇒ `stock_qty > 0`
- `pending_review` ⇒ `unit_price`, `stock_qty` and `listed_date` may be null

### 4.3 `ORDER`

| Field | Canonical type | Constraint | Observed variants |
|---|---|---|---|
| `id` → **`order_id`** | string | `^ORD-\d{4}$`, unique | `ORD-3001` appears **twice** (lines 4 and 17, identical) |
| `name` → **`customer_name`** | string | should match a `CUSTOMER.name` (fallback; future exports carry `customer_id`) | `Sofia Rossi` ≠ `Sofia Rossi 🌟`; `Wei Zhang` ≠ `  Wei Zhang  ` |
| `contact_or_sku` → **`sku`** | string | FK → `PRODUCT.sku` | All resolve; `SKU-00204` on ORD-3004 (line 13) resolves after normalisation to `SKU-2004` |
| `value` → **`unit_price`** | decimal(10,2) \| null | unit price; line total = unit_price × quantity (derived, not stored) | `19.99`, `45.00`, `$39.98`, `34.50`, `NULL` |
| `quantity` | integer \| null | ≠ 0; negative = refund (quantity of units refunded) | `2`, `-1`, `1`, `None` |
| `date` → **`order_ts`** | date or datetime | ISO 8601 | `2024/02/03 14:22:00` (only value with a time, no timezone) |
| `status` | enum `{pending, shipped, cancelled, refunded}` | — | **Missing** on ORD-3004 (row truncated) |
| `tags` | list\<string\> | vocabulary: `express` | Mostly empty |
| `notes` | string \| null | — | Escaped quotes (ORD-3002); missing on ORD-3004 |

**Rule: `unit_price` is always the unit price.**
- ORD-3001: SKU-2001 (19.99) × 2, `value = 19.99` → conforms (unit price).
- ORD-3003: SKU-2001 (19.99) × 2, `value = $39.98` → violates the rule (holds line total).
  Repair: divide by quantity → 19.99 (matches SKU-2001's price), and log as data-quality issue.

**Expected status/quantity invariant** (holds in the sample):
- `quantity < 0` ⇔ `status = refunded` (ORD-3002, line 7)

**Relationship model.** An order references its customer **by name** in this file (a fallback for legacy exports);
future exports will carry `customer_id` as the direct join key. Names are not guaranteed unique and are inconsistently
formatted, so the name-based join is lossy.

```
CUSTOMER (id) 1 ──< ORDER (customer_id; name-match fallback for legacy files)
PRODUCT  (sku) 1 ──< ORDER (contact_or_sku, exact)
CUSTOMER (id) 1 ──< CUSTOMER (referral, only in free-text notes)
```

---

## 5. Value domains and parsing rules

### 5.1 Null tokens

All of the following occur and should be read as **null**:

| Token | Where |
|---|---|
| empty string | many fields |
| `-` | `contact_or_sku` (CUST-1004) |
| `N/A` | `tags` (CUST-1002) |
| `NULL` | `value` (ORD-3005) |
| `None` | `quantity` (ORD-3005) |
| `TBD` | `value` (SKU-2003), a *deferred* value rather than an absent one |
| *(missing column)* | `status`, `tags`, `notes` on ORD-3004 |

`many` (SKU-2003 quantity) is not a null token. It is a non-numeric placeholder, so map it to
null and raise a data-quality flag.

### 5.2 Monetary values (`value`)

| Raw | Pattern | Parsed | Currency |
|---|---|---|---|
| `19.99` | plain decimal, dot | 19.99 | GBP (default) |
| `$1,240.50` | symbol + comma thousands + dot decimal | 1240.50 | USD |
| `$39.98` | symbol + dot decimal | 39.98 | USD |
| `45,00` | comma decimal | 45.00 | GBP (default) |
| `€2.345,00` | symbol + dot thousands + comma decimal | 2345.00 | EUR |
| `1.5e3` | scientific notation | 1500.00 | GBP (default) |
| ` 275.00 ` | whitespace padded | 275.00 | GBP (default) |
| `12.99 (10% off)` | number + parenthetical annotation | 12.99 (annotation → metadata) | GBP (default) |
| `TBD`, `NULL` | tokens | null | — |

**Parsing algorithm** (in order):
1. Trim whitespace; if the result is a null token, return null.
2. Remove any trailing parenthetical `\s*\(.*\)$` and keep it as an annotation.
3. Strip a leading currency symbol (`$`, `€`, `£`) and record it; if none, currency = GBP.
4. If the value matches `^[+-]?\d+(\.\d+)?[eE][+-]?\d+$`, parse it as scientific notation.
5. If it contains both `.` and `,`, the **rightmost** separator is the decimal mark.
6. If it contains only `,` followed by exactly 2 digits at the end, the comma is the decimal mark.
   Otherwise the comma separates thousands.
7. Parse the result as a decimal and round to 2 places.

Edge case: a string like `1,240` (comma followed by 3 digits, no dot) is ambiguous. It is
treated as thousands by rule 6, but that is a guess.

Unsymbolled values are GBP (the default). The 3 symbolled values (`$1,240.50` line 2, `€2.345,00` line 8, `$39.98` line 10)
are foreign-currency exceptions. Keep the source currency and raw amount, convert to GBP, and log a data-quality issue.
Never just relabel them as GBP. GBP figures are for dashboards and reporting, not accounting, so rates come from the
ECB euro reference rates (cross-rated via EUR, e.g. USD→GBP = EUR/GBP ÷ EUR/USD). Which rate to use depends on the field:

| Field | Rate date | Issue code |
|---|---|---|
| `ORDER.unit_price` | the order date (`order_ts`); if the ECB published nothing that day, the latest earlier rate | `FX_CONVERTED_AT_ORDER_DATE` |
| `CUSTOMER.lifetime_spend` | the latest ECB rate published **before the run's date** (the run's rate snapshot). The signup date is not used: lifetime spend builds up over many purchases whose dates are unknown, so no single historical date is correct. The figure is approximate and the dashboard marks it with "≈". | `FX_CONVERTED_AT_RUN_DATE` |

Store the rate, the date it applies to and its source on the row, so a re-run gives the same result. If no rate is
available, set the GBP value to null, keep the source amount, and raise `FX_RATE_UNAVAILABLE` (the row goes to human review).
ORD-3003 is first repaired to a unit price (`$39.98` ÷ 2 = `$19.99`) and then converted.

### 5.3 Dates (`date`)

| Raw | Format | Regex | Parsed |
|---|---|---|---|
| `2023-05-12` | ISO `YYYY-MM-DD` | `^\d{4}-\d{2}-\d{2}$` | 2023-05-12 |
| `01/22/2023` | US `MM/DD/YYYY` | `^\d{2}/\d{2}/\d{4}$` | 2023-01-22 |
| `15-Jan-2024` | `DD-Mon-YYYY` | `^\d{2}-[A-Za-z]{3}-\d{4}$` | 2024-01-15 |
| `2024/02/03 14:22:00` | `YYYY/MM/DD HH:MM:SS` | `^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$` | 2024-02-03T14:22:00 (no timezone) |
| ` 2023-08-01 ` | ISO, padded | trim first | 2023-08-01 |
| *(empty)* | — | — | null |

Slash dates `NN/NN/YYYY` are always month/day/year (US format). So `03/04/2023` = 4 March 2023. The format stays a supported input;
parse it deterministically and don't flag it as ambiguous.

### 5.4 Status normalisation

| Record type | Raw → Canonical |
|---|---|
| CUSTOMER | `Active`, `ACTIVE`, `active` → `active`; `inactive` → `inactive`; `Y` → `active` (confirmed) |
| PRODUCT | already canonical |
| ORDER | already canonical; missing → null (flag it) |

### 5.5 Tags

- Syntax: `^[a-z]+(\|[a-z]+)*$`, split on `|`.
- Empty string or `N/A` → `[]`.
- Tags are all lowercase in the sample. Normalise them to lowercase anyway.

### 5.6 Free text (`name`, `notes`)

- Trim leading and trailing whitespace (`  Wei Zhang  `, ` Prefers SMS`).
- Preserve internal punctuation, apostrophes, `&`, accents and newlines.
- For **matching** only (not storage), compute a key: Unicode NFKC, then strip symbols and
  emoji (category `So`), then collapse whitespace, then casefold.
  This matches `Sofia Rossi 🌟` to `Sofia Rossi`.

---

## 6. Exception catalogue

### 6.1 Structural

| Line(s) | Record | Issue | Effect on a strict parser | Recommended repair |
|---|---|---|---|---|
| 1 | header | UTF-8 BOM | first column named `﻿record_type` | open with `utf-8-sig` |
| 13 | ORD-3004 | 7 fields; `status`, `tags`, `notes` missing | short row; pandas fills with NaN | pad with nulls and flag `status` missing |
| 14 | CUST-1005 | 12 fields; `notes` contains unquoted commas | pandas raises `ParserError` | since `notes` is the last column, re-join fields 10…n with `,` → `Called twice, no answer, will retry next week` |
| 15–16 | SKU-2005 | quoted field contains a newline | fine for RFC 4180 parsers, breaks line-based tools (`wc -l`, `grep`, `split`) | keep; escape as needed downstream |
| 17 | ORD-3001 | exact duplicate of line 4 | double counting | deduplicate on the whole row, then check `id` is unique |
| 18 | — | blank line | empty record | skip |
| 19 | header | repeated header | read as a data row with `record_type = "record_type"` | skip any row equal to the header |
| 24 | SKU-2007 | 11 fields; trailing comma | pandas raises `ParserError` | drop trailing fields if they are empty |

### 6.2 Semantic / value-level

| Line | Record | Field | Issue |
|---|---|---|---|
| 2 | CUST-1001 | value | `$1,240.50`: USD (non-GBP exception) with thousands separator |
| 5 | CUST-1002 | date | `01/22/2023`: US format (confirmed MM/DD/YYYY) |
| 5 | CUST-1002 | status | `ACTIVE`: case variant |
| 5 | CUST-1002 | tags | `N/A` null token |
| 5 | CUST-1002 | notes | implicit FK to CUST-1001 in free text |
| 6 | SKU-2002 | value | `45,00`: decimal comma |
| 7 | ORD-3002 | quantity | `-1`: negative quantity confirmed to mean refund (1 unit refunded) |
| 7 | ORD-3002 | notes | escaped `""` quotes |
| 8 | CUST-1003 | value | `€2.345,00`: EUR (non-GBP exception) with EU number format |
| 8 | CUST-1003 | date | `15-Jan-2024`: named month |
| 8 | CUST-1003 | status | `inactive` but has an order dated 2024-02-03 (ORD-3003). Plausible, but worth checking |
| 9 | SKU-2003 | value / quantity / date | `TBD` / `many` / empty: product not finalised |
| 10 | ORD-3003 | value | `$39.98` (USD, non-GBP exception) violates unit-price rule (holds line total). Repair: ÷ qty 2 → 19.99. |
| 10 | ORD-3003 | date | datetime with `/` separators, no timezone |
| 11 | CUST-1004 | contact | `-` placeholder, so no email |
| 11 | CUST-1004 | value | `1.5e3`: scientific notation |
| 12 | SKU-00204 | id | 5-digit zero-padded SKU, typo. Repair: → `SKU-2004` (applied to PRODUCT and ORD-3004 reference). |
| 12 | SKU-00204 | quantity | `-12`: negative stock (backorder), confirmed by notes |
| 20 | CUST-1006 | name / value / date / notes | leading/trailing whitespace |
| 20 | CUST-1006 | status | `Y`: confirmed to mean `active` |
| 21 | SKU-2006 | value | `12.99 (10% off)`: annotation inside a numeric field |
| 22 | ORD-3005 | value / quantity | `NULL` / `None` null tokens (the order was cancelled) |
| 23 | CUST-1007 | name | emoji `🌟`, so the order name `Sofia Rossi` (line 25) doesn't match exactly |
| 25 | ORD-3006 | status | `pending`, "awaiting stock", while SKU-2007 shows `in_stock` with 60 units. Minor contradiction |

---

## 7. Proposed normalised target schema

Split the union into three typed tables:

```sql
CREATE TABLE customer (
  customer_id     TEXT PRIMARY KEY CHECK (customer_id ~ '^CUST-\d{4}$'),
  name            TEXT NOT NULL,
  email           TEXT,
  lifetime_spend  NUMERIC(12,2),
  currency        CHAR(3) NOT NULL DEFAULT 'GBP',
  source_currency CHAR(3),               -- set only when converted from a non-GBP value
  source_amount   NUMERIC(12,2),         -- set only when converted from a non-GBP value
  fx_rate         NUMERIC(18,8),         -- source→GBP rate used (see §5.2)
  fx_rate_date    DATE,                  -- ECB publication date of that rate
  fx_source       TEXT,                  -- e.g. 'ECB'
  signup_date     DATE,
  status          TEXT CHECK (status IN ('active','inactive')),
  tags            TEXT[] NOT NULL DEFAULT '{}',
  notes           TEXT,
  referred_by     TEXT REFERENCES customer(customer_id)   -- extracted from notes
);

CREATE TABLE product (
  sku             TEXT PRIMARY KEY CHECK (sku ~ '^SKU-\d{4}$'),  -- SKU-00204 normalised to SKU-2004 on load
  name            TEXT NOT NULL,
  category        TEXT NOT NULL,
  unit_price      NUMERIC(10,2),
  price_note      TEXT,                  -- e.g. '10% off'
  currency        CHAR(3) NOT NULL DEFAULT 'GBP',
  stock_qty       INTEGER,               -- negative = backorder
  listed_date     DATE,
  status          TEXT CHECK (status IN ('in_stock','discontinued','pending_review','backordered')),
  tags            TEXT[] NOT NULL DEFAULT '{}',
  notes           TEXT
);

CREATE TABLE "order" (
  order_id        TEXT PRIMARY KEY CHECK (order_id ~ '^ORD-\d{4}$'),
  customer_id     TEXT REFERENCES customer(customer_id), -- supplied directly in future exports; resolved by name for legacy files
  customer_name_raw TEXT NOT NULL,
  sku             TEXT NOT NULL REFERENCES product(sku) CHECK (sku ~ '^SKU-\d{4}$'),  -- SKU-00204 normalised to SKU-2004 on load
  unit_price      NUMERIC(10,2),
  quantity        INTEGER CHECK (quantity <> 0),         -- negative = refund
  currency        CHAR(3) NOT NULL DEFAULT 'GBP',
  source_currency CHAR(3),               -- set only when converted from a non-GBP value
  source_amount   NUMERIC(12,2),         -- set only when converted from a non-GBP value
  fx_rate         NUMERIC(18,8),         -- source→GBP rate used (see §5.2)
  fx_rate_date    DATE,                  -- ECB publication date of that rate
  fx_source       TEXT,                  -- e.g. 'ECB'
  ordered_at      TIMESTAMP,             -- no timezone in source
  status          TEXT CHECK (status IN ('pending','shipped','cancelled','refunded')),
  tags            TEXT[] NOT NULL DEFAULT '{}',
  notes           TEXT
);
```

Keep a side table `data_quality_issue(source_line, record_id, field, raw_value, issue_code)`
for every coercion, so the original raw values stay auditable.