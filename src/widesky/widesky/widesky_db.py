"""WideSky Database Class and User Lookup Functionality"""

import asyncio
import logging
import os
from types import NoneType

import httpx
from aiocache import Cache, cached
from psycopg_pool import AsyncConnectionPool
from tenacity import retry, wait_exponential

CACHE_TTL = 3600
RETRY_MULT = 1.1
RETRY_MIN = 0.1
RETRY_MAX = 10


class WideSkyUserLookup:
    async def create(self):
        self.httpx_client = httpx.AsyncClient()

    @cached(ttl=CACHE_TTL, cache=Cache.MEMORY, namespace="user_lookup")
    @retry(wait=wait_exponential(multiplier=RETRY_MULT, min=RETRY_MIN, max=RETRY_MAX))
    async def lookup_user(self, did: str) -> tuple:
        r = await self.httpx_client.get(f"https://plc.directory/{did}")
        if r.status_code == 200:
            aka = r.json()["alsoKnownAs"]
            return aka[0], aka
        else:
            raise ConnectionError(f"Lookup user was unable to access plc.directory for did {did}")

    async def close(self):
        await self.httpx_client.aclose()
        logging.info("HTTPX client closed gracefully.")


class WideSkyPSQL:
    def __init__(self, num_workers=5, batch_size=100, batch_timeout=3.0, queue_length=1000):
        self._process_queue = asyncio.Queue(queue_length)
        self._workers = []
        self._num_workers = num_workers
        self._batch_size = batch_size
        self._batch_timeout = batch_timeout
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self._workers.append(worker)

    async def create(self, clear_database=False):
        self._user_lookup = WideSkyUserLookup()
        await self._user_lookup.create()

        PG_HOST = os.getenv("PG_HOST", "db")
        PG_DB = os.getenv("PG_DB", "bluesky")
        PG_USER = os.getenv("PG_USER", "postgres")
        PG_PASS = os.getenv("PG_PASS", "postgres")
        self._pool = AsyncConnectionPool(
            conninfo=f"host={PG_HOST} dbname={PG_DB} user={PG_USER} password={PG_PASS}",
            max_size=self._num_workers,
            open=False,
        )
        await self._pool.open()

        await self._ensure_users_table(clear_database=clear_database)
        await self._ensure_posts_table(clear_database=clear_database)
        await self._ensure_reposts_table(clear_database=clear_database)
        await self._ensure_likes_table(clear_database=clear_database)

    async def close(self):
        await self._user_lookup.close()
        await self._pool.close()
        for _ in self._workers:
            await self._process_queue.put(-1)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._process_queue.task_done()
        logging.info("Postgres module closed gracefully.")

    def metrics(self):
        return self._process_queue.qsize()

    async def _worker(self, name: str):
        user_batch: list[str] = []
        post_batch: list[dict] = []
        repost_batch: list[dict] = []
        like_batch: list[dict] = []
        last_flush = asyncio.get_event_loop().time()
        while True:
            now = asyncio.get_event_loop().time()
            try:
                timeout = max(0, self._batch_timeout - (now - last_flush))
                try:
                    queued_message = await asyncio.wait_for(self._process_queue.get(), timeout)
                except asyncio.TimeoutError:
                    queued_message = None
                if queued_message:
                    if queued_message == -1:
                        break
                    match queued_message["request"]:
                        case "insert_user":
                            user_batch.append(queued_message["data"])
                            self._process_queue.task_done()
                        case "insert_post":
                            post_batch.append(queued_message["data"])
                            self._process_queue.task_done()
                        case "insert_repost":
                            repost_batch.append(queued_message["data"])
                            self._process_queue.task_done()
                        case "insert_like":
                            like_batch.append(queued_message["data"])
                            self._process_queue.task_done()
                (
                    user_batch,
                    post_batch,
                    repost_batch,
                    like_batch,
                    last_flush,
                ) = await self._process_batches(
                    user_batch,
                    post_batch,
                    repost_batch,
                    like_batch,
                    queued_message is None,
                    last_flush,
                )
            except Exception as e:
                logging.exception(f"Exception in {name} queue: {e}")

    async def _process_batches(
        self,
        user_batch: list[str],
        post_batch: list[dict],
        repost_batch: list[dict],
        like_batch: list[dict],
        commit: bool,
        last_flush: float,
    ) -> tuple[list[str], list[dict], list[dict], list[dict], float]:
        now = asyncio.get_event_loop().time()
        if len(user_batch) >= self._batch_size or (commit and user_batch):
            await self._batch_insert_user(user_batch)
            user_batch.clear()
            last_flush = now
        if len(post_batch) >= self._batch_size or (commit and post_batch):
            await self._batch_insert_post(post_batch)
            post_batch.clear()
            last_flush = now
        if len(repost_batch) >= self._batch_size or (commit and repost_batch):
            await self._batch_insert_repost(repost_batch)
            repost_batch.clear()
            last_flush = now
        if len(like_batch) >= self._batch_size or (commit and like_batch):
            await self._batch_insert_like(like_batch)
            like_batch.clear()
            last_flush = now
        if last_flush == now:
            logging.debug(f"{self._process_queue.qsize()} items in queue after a flush")
        return user_batch, post_batch, repost_batch, like_batch, last_flush

    def _insert_user(self, did: str) -> None:
        self._process_queue.put_nowait({"request": "insert_user", "data": did})

    def _insert_post(self, storage_frame: dict) -> None:
        self._process_queue.put_nowait({"request": "insert_post", "data": storage_frame})

    def _insert_repost(self, storage_frame: dict) -> None:
        self._process_queue.put_nowait({"request": "insert_repost", "data": storage_frame})

    def _insert_like(self, storage_frame: dict) -> None:
        self._process_queue.put_nowait({"request": "insert_like", "data": storage_frame})

    async def _batch_insert_user(self, batch: list[str]) -> None:
        if not batch:
            return
        processed_batch = []
        for user_request in batch:
            ifexists = await self._check_user(user_request)
            if ifexists is not None:
                first_known, alternates = ifexists
                processed_batch.append(
                    {
                        "did": user_request,
                        "first_known_as": first_known,
                        "also_known_as_full": alternates,
                    }
                )
        if len(processed_batch) > 0:
            try:
                async with self._pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.executemany(
                            """
                            INSERT INTO users (
                                did, first_known_as, also_known_as_full
                            )
                            VALUES (
                                %(did)s, %(first_known_as)s, %(also_known_as_full)s
                            )
                            ON CONFLICT (did) DO UPDATE
                                SET also_known_as_full = CASE
                                    WHEN cardinality(EXCLUDED.also_known_as_full) > cardinality(users.also_known_as_full)
                                    THEN EXCLUDED.also_known_as_full
                                    ELSE users.also_known_as_full
                                END;
                            """,
                            processed_batch,
                        )
                        logging.info(f"Added {len(processed_batch)} users to Postgres")
                    await conn.commit()
            except Exception as e:
                logging.exception(f"Batch insert_user failed: {e} — {batch[:3]}")

    async def _batch_insert_post(self, batch: list[dict]) -> None:
        if not batch:
            return
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """
                        INSERT INTO posts (
                            cid, created_at, did, commit, text, langs, facets,
                            has_embed, embed_type, embed_refs, external_uri,
                            has_record, record_cid, record_uri, is_reply,
                            reply_root_cid, reply_root_uri, reply_parent_cid, reply_parent_uri
                        )
                        VALUES (
                            %(cid)s, %(created_at)s, %(did)s, %(commit)s, %(text)s, %(langs)s,
                            %(facets)s, %(has_embed)s, %(embed_type)s, %(embed_refs)s,
                            %(external_uri)s, %(has_record)s, %(record_cid)s, %(record_uri)s,
                            %(is_reply)s, %(reply_root_cid)s, %(reply_root_uri)s,
                            %(reply_parent_cid)s, %(reply_parent_uri)s
                        )
                        ON CONFLICT DO NOTHING;
                        """,
                        batch,
                    )
                logging.info(f"Added {len(batch)} posts to Postgres")
                await conn.commit()
        except Exception as e:
            logging.exception(f"Batch insert_post failed: {e} — {batch[:3]}")

    async def _batch_insert_repost(self, batch: list[dict]) -> None:
        if not batch:
            return
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """
                        INSERT INTO reposts (
                            cid, created_at, did, commit, subject_cid, subject_uri
                        )
                        VALUES (
                            %(cid)s, %(created_at)s, %(did)s, %(commit)s, %(subject_cid)s, %(subject_uri)s
                        )
                        ON CONFLICT DO NOTHING;
                        """,
                        batch,
                    )
                logging.info(f"Added {len(batch)} reposts to Postgres")
                await conn.commit()
        except Exception as e:
            logging.exception(f"Batch insert_post failed: {e} — {batch[:3]}")

    async def _batch_insert_like(self, batch: list[dict]) -> None:
        if not batch:
            return
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        """
                        INSERT INTO likes (
                            cid, created_at, did, commit, subject_cid, subject_uri
                        )
                        VALUES (
                            %(cid)s, %(created_at)s, %(did)s, %(commit)s, %(subject_cid)s, %(subject_uri)s
                        )
                        ON CONFLICT DO NOTHING;
                        """,
                        batch,
                    )
                    logging.info(f"Added {len(batch)} likes to Postgres")
                await conn.commit()
        except Exception as e:
            logging.exception(f"Batch insert_post failed: {e} — {batch[:3]}")

    async def _ensure_users_table(self, clear_database=False) -> None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                if clear_database:
                    await cur.execute("""
                        DROP TABLE IF EXISTS users;
                    """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        did TEXT PRIMARY KEY,
                        first_known_as TEXT,
                        also_known_as_full TEXT ARRAY
                    );
                """)
            await conn.commit()
            logging.info("Ensured users table exists")

    async def _ensure_likes_table(self, clear_database=False) -> None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                if clear_database:
                    await cur.execute("""
                        DROP TABLE IF EXISTS likes;
                    """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS likes (
                        cid TEXT PRIMARY KEY,
                        created_at TIMESTAMP WITH TIME ZONE,
                        did TEXT,
                        commit TEXT,
                        subject_cid TEXT,
                        subject_uri TEXT
                    );
                """)
                await conn.commit()
            logging.info("Ensured likes table exists")

    async def _ensure_posts_table(self, clear_database=False) -> None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                if clear_database:
                    await cur.execute("""
                        DROP TABLE IF EXISTS posts;
                    """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS posts (
                        cid TEXT PRIMARY KEY,
                        created_at TIMESTAMP WITH TIME ZONE,
                        did TEXT,
                        commit TEXT,
                        text TEXT,
                        langs TEXT ARRAY,
                        facets JSONB,
                        has_embed BOOLEAN,
                        embed_type TEXT,
                        embed_refs TEXT ARRAY,
                        external_uri TEXT,
                        has_record BOOLEAN,
                        record_cid TEXT,
                        record_uri TEXT,
                        is_reply BOOLEAN,
                        reply_root_cid TEXT,
                        reply_root_uri TEXT,
                        reply_parent_cid TEXT,
                        reply_parent_uri TEXT
                    );
                """)
            await conn.commit()
            logging.info("Ensured posts table exists")

    async def _ensure_reposts_table(self, clear_database=False) -> None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                if clear_database:
                    await cur.execute("""
                        DROP TABLE IF EXISTS reposts;
                    """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS reposts (
                        cid TEXT PRIMARY KEY,
                        created_at TIMESTAMP WITH TIME ZONE,
                        did TEXT,
                        commit TEXT,
                        subject_cid TEXT,
                        subject_uri TEXT
                    );
                """)
            await conn.commit()
            logging.info("Ensured reposts table exists")

    async def _check_user(self, did: str) -> tuple | None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM users WHERE did = %s LIMIT 1", (did,))
                user_exists = await cur.fetchone()
        return None if user_exists else await self._user_lookup.lookup_user(did)
