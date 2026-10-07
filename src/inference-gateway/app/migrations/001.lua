local version = redis.call('GET', KEYS[1])
if version and version ~= '0' and version ~= '1' then
  return redis.error_reply('Unsupported gateway schema; use the matching image or restore a pre-upgrade backup')
end
-- 0.2.0 had no version marker. Adopt its existing keys without resetting accounting or TTLs.
redis.call('SET', KEYS[1], '1')
return 1
