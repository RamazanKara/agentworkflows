local version = redis.call('GET', KEYS[1])
if version == '2' then return 2 end
if version ~= '1' then
  return redis.error_reply('Unsupported gateway schema; use the matching image or restore a pre-upgrade backup')
end
-- Managed keys, login transactions, and sessions use new keys under the existing prefix.
redis.call('SET', KEYS[1], '2')
return 2
