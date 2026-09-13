UPDATE market_observations
SET eligible = FALSE
WHERE validation_reason IN (
  'order_book_disabled',
  'unsupported_outcome_labels',
  'missing_order_book',
  'missing_no_order_book',
  'missing_order_book_ask',
  'missing_no_order_book_ask',
  'invalid_order_book',
  'invalid_no_order_book',
  'incoherent_book_timestamps'
);
