"""Result caching and best-effort popularity statistics in Redis."""

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from wordcount.counting import RULE_VERSION

POPULARITY_KEY = "wc:keyword_popularity"


class ResultCache:
    def __init__(self, host="redis", port=6379, ttl=3600):
        self.ttl = ttl
        self.client = redis.Redis(
            host=host, port=port, decode_responses=True,
            socket_connect_timeout=0.2, socket_timeout=0.2,
            retry=Retry(NoBackoff(), 0),
        )

    @staticmethod
    def key(document_hash, keyword):
        return f"wc:result:{RULE_VERSION}:{document_hash}:{keyword}"

    def get(self, document_hash, keyword):
        value = self.client.get(self.key(document_hash, keyword))
        if value is None:
            return None
        # Invalid cache entries must not produce incorrect answers.
        try:
            count = int(value)
            if count < 0:
                raise ValueError
        except ValueError:
            return None
        return count

    def put(self, document_hash, keyword, count):
        self.client.set(self.key(document_hash, keyword), count, ex=self.ttl)

    def record_keyword(self, keyword):
        self.client.zincrby(POPULARITY_KEY, 1, keyword)

    def top_keywords(self, limit):
        entries = self.client.zrevrange(POPULARITY_KEY, 0, limit - 1, withscores=True)
        return [{"keyword": word, "requests": int(score)} for word, score in entries]
