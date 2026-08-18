-- AAVE/GHO ERC-20 transfer extraction from the public Ethereum BigQuery dataset.
-- Parameters are supplied by src/aave_bns/query.py; do not interpolate untrusted strings.
SELECT
  block_timestamp AS timestamp,
  block_number,
  transaction_hash AS tx_hash,
  log_index,
  1 AS chain_id,
  LOWER(token_address) AS token_address,
  LOWER(from_address) AS from_address,
  LOWER(to_address) AS to_address,
  CAST(value AS STRING) AS raw_value
FROM `bigquery-public-data.crypto_ethereum.token_transfers`
WHERE DATE(block_timestamp) BETWEEN @start_date AND @end_date
  AND LOWER(token_address) IN UNNEST(@token_addresses)
ORDER BY block_number, log_index
