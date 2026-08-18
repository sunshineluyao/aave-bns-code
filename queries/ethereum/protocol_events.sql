-- Optional protocol-event query scaffold.
-- Event signatures and contract lists must be generated from a pinned Aave Address Book release.
SELECT
  block_timestamp AS timestamp,
  block_number,
  transaction_hash AS tx_hash,
  log_index,
  LOWER(address) AS contract_address,
  topics,
  data
FROM `bigquery-public-data.crypto_ethereum.logs`
WHERE DATE(block_timestamp) BETWEEN @start_date AND @end_date
  AND LOWER(address) IN UNNEST(@contract_addresses)
ORDER BY block_number, log_index
