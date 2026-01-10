# Production Deployment Guide

This guide covers deploying **Crader** in production environments with focus on performance, reliability, and scalability.

## Architecture Overview

```
┌─────────────────┐
│   Load Balancer │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
┌───▼───┐ ┌──▼────┐
│ API 1 │ │ API 2 │  (Readers - Stateless)
└───┬───┘ └──┬────┘
    │        │
    └────┬───┘
         │
┌────────▼────────┐
│   PostgreSQL    │  (Primary + Replicas)
│   + pgvector    │
└────────┬────────┘
         │
┌────────▼────────┐
│  Worker Pool    │  (Writers - Indexing)
└─────────────────┘
```

---

## PostgreSQL Tuning

### Hardware Requirements

| Scale | CPU | RAM | Storage | IOPS |
|-------|-----|-----|---------|------|
| **Small** (100K chunks) | 4 cores | 8GB | 50GB SSD | 1K |
| **Medium** (1M chunks) | 8 cores | 32GB | 200GB SSD | 5K |
| **Large** (10M chunks) | 16 cores | 64GB | 1TB NVMe | 20K |

### Configuration

```ini
# postgresql.conf

# Memory Settings
shared_buffers = 8GB              # 25% of RAM
effective_cache_size = 24GB       # 75% of RAM
work_mem = 256MB                  # For sorting/hashing
maintenance_work_mem = 2GB        # For VACUUM, CREATE INDEX

# Checkpoint Settings
checkpoint_timeout = 15min
checkpoint_completion_target = 0.9
max_wal_size = 4GB

# Connection Settings
max_connections = 200
shared_preload_libraries = 'pg_stat_statements,pgvector'

# Query Planner
random_page_cost = 1.1            # For SSD
effective_io_concurrency = 200    # For SSD

# Parallel Query
max_parallel_workers_per_gather = 4
max_parallel_workers = 8
```

### Vector Index Optimization

```sql
-- For datasets < 1M vectors: IVFFlat
CREATE INDEX idx_embeddings_ivfflat 
ON node_embeddings 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- For datasets > 1M vectors: HNSW
CREATE INDEX idx_embeddings_hnsw 
ON node_embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Tune HNSW search
SET hnsw.ef_search = 100;  -- Higher = more accurate, slower
```

### Index Maintenance

```sql
-- Regular maintenance
VACUUM ANALYZE node_embeddings;
VACUUM ANALYZE nodes;
VACUUM ANALYZE edges;

-- Rebuild indexes periodically
REINDEX INDEX CONCURRENTLY idx_embeddings_hnsw;

-- Update statistics
ANALYZE;
```

---

## Connection Pooling

### PgBouncer Configuration

```ini
# pgbouncer.ini

[databases]
codebase = host=localhost port=5432 dbname=codebase

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt

# Pool settings
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
reserve_pool_size = 5
reserve_pool_timeout = 3

# Performance
server_idle_timeout = 600
server_lifetime = 3600
```

### Application Configuration

```python
from code_graph_indexer.storage.connector import PooledConnector

connector = PooledConnector(
    db_url="postgresql://user:pass@pgbouncer:6432/codebase",
    min_size=10,
    max_size=50,
    max_queries=50000,  # Recycle connections
    max_inactive_connection_lifetime=300
)
```

---

## Scaling Strategies

### Horizontal Scaling

#### Read Replicas

```python
# Primary for writes
primary_connector = PooledConnector(
    db_url="postgresql://user:pass@primary:5432/codebase"
)

# Replica for reads
replica_connector = PooledConnector(
    db_url="postgresql://user:pass@replica:5432/codebase"
)

# Use replica for search
retriever = CodeRetriever(replica_connector)

# Use primary for indexing
indexer = CodebaseIndexer(
    repo_path="./repo",
    storage_connector=primary_connector
)
```

#### Load Balancing

```nginx
# nginx.conf
upstream api_servers {
    least_conn;
    server api1:8000 weight=1;
    server api2:8000 weight=1;
    server api3:8000 weight=1;
}

server {
    listen 80;
    
    location / {
        proxy_pass http://api_servers;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Vertical Scaling

#### PostgreSQL Partitioning

```sql
-- Partition by repository
CREATE TABLE nodes_partitioned (
    id UUID,
    repository_id UUID,
    file_path TEXT,
    content_hash TEXT,
    metadata JSONB
) PARTITION BY HASH (repository_id);

-- Create partitions
CREATE TABLE nodes_part_0 PARTITION OF nodes_partitioned
    FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE nodes_part_1 PARTITION OF nodes_partitioned
    FOR VALUES WITH (MODULUS 4, REMAINDER 1);
-- ... etc
```

---

## Monitoring

### Metrics to Track

```python
from prometheus_client import Counter, Histogram, Gauge

# Request metrics
search_requests = Counter('search_requests_total', 'Total search requests')
search_duration = Histogram('search_duration_seconds', 'Search duration')

# Database metrics
db_connections = Gauge('db_connections_active', 'Active DB connections')
query_duration = Histogram('db_query_duration_seconds', 'Query duration')

# Indexing metrics
chunks_indexed = Counter('chunks_indexed_total', 'Total chunks indexed')
indexing_duration = Histogram('indexing_duration_seconds', 'Indexing duration')
```

### PostgreSQL Monitoring

```sql
-- Active queries
SELECT pid, usename, state, query, now() - query_start AS duration
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY duration DESC;

-- Index usage
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;

-- Cache hit ratio
SELECT 
    sum(heap_blks_read) as heap_read,
    sum(heap_blks_hit) as heap_hit,
    sum(heap_blks_hit) / (sum(heap_blks_hit) + sum(heap_blks_read)) as ratio
FROM pg_statio_user_tables;
```

### Grafana Dashboards

Key metrics to visualize:
- Query latency (p50, p95, p99)
- Throughput (requests/second)
- Error rate
- Database connections
- Cache hit ratio
- Index usage

---

## Backup Strategy

### Continuous Archiving

```bash
# postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'cp %p /backup/wal/%f'
```

### Base Backups

```bash
#!/bin/bash
# backup.sh

# Full backup
pg_basebackup -D /backup/base/$(date +%Y%m%d) \
    -Ft -z -P -h localhost -U postgres

# Retention: keep last 7 days
find /backup/base -type d -mtime +7 -exec rm -rf {} \;
```

### Point-in-Time Recovery

```bash
# recovery.conf
restore_command = 'cp /backup/wal/%f %p'
recovery_target_time = '2024-01-01 12:00:00'
```

---

## Security

### Network Security

```python
# Use SSL for database connections
connector = PooledConnector(
    db_url="postgresql://user:pass@host:5432/db?sslmode=require"
)
```

### Access Control

```sql
-- Create read-only user for API
CREATE ROLE api_reader WITH LOGIN PASSWORD 'secure_password';
GRANT CONNECT ON DATABASE codebase TO api_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO api_reader;

-- Create write user for indexer
CREATE ROLE indexer_writer WITH LOGIN PASSWORD 'secure_password';
GRANT CONNECT ON DATABASE codebase TO indexer_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO indexer_writer;
```

### API Authentication

```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def verify_token(credentials = Depends(security)):
    if credentials.credentials != os.getenv("API_TOKEN"):
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials
```

---

## Docker Deployment

### docker-compose.yml

```yaml
version: '3.8'

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: codebase
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    command: >
      postgres
      -c shared_buffers=2GB
      -c effective_cache_size=6GB
      -c work_mem=128MB

  pgbouncer:
    image: pgbouncer/pgbouncer:latest
    environment:
      DATABASES_HOST: postgres
      DATABASES_PORT: 5432
      DATABASES_DBNAME: codebase
      PGBOUNCER_POOL_MODE: transaction
      PGBOUNCER_MAX_CLIENT_CONN: 1000
    ports:
      - "6432:6432"
    depends_on:
      - postgres

  api:
    build: .
    environment:
      DB_URL: postgresql://postgres:${DB_PASSWORD}@pgbouncer:6432/codebase
      EMBEDDING_PROVIDER: openai
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    ports:
      - "8000:8000"
    depends_on:
      - pgbouncer
    deploy:
      replicas: 3

volumes:
  postgres_data:
```

---



## Troubleshooting

### High CPU Usage

```sql
-- Find expensive queries
SELECT query, calls, total_time, mean_time
FROM pg_stat_statements
ORDER BY total_time DESC
LIMIT 10;
```

### High Memory Usage

```sql
-- Check work_mem usage
SELECT name, setting, unit
FROM pg_settings
WHERE name IN ('work_mem', 'maintenance_work_mem', 'shared_buffers');
```

### Slow Queries

```sql
-- Enable query logging
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- Log queries > 1s
SELECT pg_reload_conf();
```

---

## Disaster Recovery

### Failover Plan

1. **Detect failure**: Monitor primary database
2. **Promote replica**: `pg_ctl promote`
3. **Update connection strings**: Point to new primary
4. **Verify data integrity**: Check replication lag

### Recovery Checklist

- [ ] Restore from backup
- [ ] Replay WAL logs
- [ ] Verify data integrity
- [ ] Update DNS/load balancer
- [ ] Test application connectivity
- [ ] Monitor for errors

---

## Cost Optimization

### Database Costs

- Use managed PostgreSQL (AWS RDS, GCP Cloud SQL) for easier management
- Enable auto-scaling for read replicas
- Use reserved instances for predictable workloads

### Embedding Costs

- Cache embeddings aggressively (30-50% savings)
- Use incremental updates (90% savings)
- Consider local models for development

### Infrastructure Costs

- Use spot instances for indexing workers
- Scale down during off-peak hours
- Implement request caching

---

## Next Steps

- [Monitoring Setup](monitoring.md): Detailed monitoring guide
- [API Reference](../reference/indexer.md): Complete API documentation
- [Scaling Guide](scaling.md): Advanced scaling strategies
