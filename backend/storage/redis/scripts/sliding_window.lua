local key = KEYS[1]
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]

local redis_time = redis.call('TIME')
local now_ms = redis_time[1] * 1000 + math.floor(redis_time[2] / 1000)
local oldest_allowed = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', key, '-inf', oldest_allowed)
local request_count = redis.call('ZCARD', key)

if request_count >= limit then
    local oldest_request = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = math.max(1, math.ceil((oldest_request[2] + window_ms - now_ms) / 1000))
    return {0, retry_after, 0}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms + 1000)
return {1, 0, limit - request_count - 1}
