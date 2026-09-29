# AiTrading — Database Setup

## 1. Install PostgreSQL 15+ with pgvector

Windows: install PostgreSQL via the official installer, then build/install the
`pgvector` extension (prebuilt binaries are available for recent PostgreSQL
versions — see https://github.com/pgvector/pgvector#installation).

## 2. Create the database and role

```sql
CREATE ROLE aitrading WITH LOGIN PASSWORD 'aitrading';
CREATE DATABASE aitrading OWNER aitrading;
```

## 3. Apply the schema

```bash
psql -U aitrading -d aitrading -f config/database.sql
```

This creates all tables, the `vector` extension, and indexes (including the
`ivfflat` cosine-similarity index used by `trading_memory` for semantic
search over past trade setups).

## 4. Point the backend at it

Set `DATABASE_URL` in `backend/.env`:

```
DATABASE_URL=postgresql://aitrading:aitrading@localhost:5432/aitrading
```

`backend/database.py` will also auto-create any missing tables on startup
via `init_db()`, so `database.sql` is the source of truth but not strictly
required if you're fine with default types.
