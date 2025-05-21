"""WideSky Processing Class for Firehose frames"""

import asyncio
import json
import logging
from types import NoneType

from firehose_utils import JSONEncoderWithBytes, read_firehose_frame
from widesky_db import WideSkyPSQL


class WideSkyProcessor:
    def __init__(self, num_workers: int = 5, queue_length=1000):
        self._wisp = WideSkyPSQL()
        self._process_queue = asyncio.Queue(queue_length)
        self._workers = []
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self._workers.append(worker)

    async def create(self):
        await self._wisp.create()

    async def close(self):
        await self._wisp.close()
        for _ in self._workers:
            await self._process_queue.put(-1)
        await asyncio.gather(*self._workers, return_exceptions=True)
        logging.info("Processor closed gracefully.")

    def metrics(self):
        return self._process_queue.qsize()

    def process_message(self, msg: dict) -> None:
        self._process_queue.put_nowait(msg)

    async def _worker(self, name: str):
        while True:
            try:
                queued_message = await self._process_queue.get()
                if queued_message == -1:
                    break
                logging.debug(f"WideSky Processor {name} starting processing message")
                await self._process_message(queued_message)
                logging.debug(f"WideSky Processor {name} completed processing message")
                self._process_queue.task_done()
            except Exception as e:
                logging.exception(f"Exception in WideSkyProcessor worker-{name}: {e}")

    async def _process_message(self, msg: bytes) -> None:
        try:
            header, body = read_firehose_frame(msg)
            logging.debug(json.dumps(body, cls=JSONEncoderWithBytes))

            header_type = header.get("t")
            match header_type:
                case "#commit":
                    ops = body.get("ops")
                    self._wisp._insert_user(str(body.get("repo")))
                    post_cids, repost_cids, like_cids = self._process_ops(ops)
                    if len(post_cids) > 0:
                        self._process_post(post_cids, body)
                    if len(repost_cids) > 0:
                        self._process_repost(repost_cids, body)
                    if len(like_cids) > 0:
                        self._process_like(like_cids, body)
                case _:
                    logging.warning(f"Unhandled header_type in _process_message: {header_type}")
        except Exception as e:
            logging.exception(f"Error processing message: {e}")

    def _process_ops(self, ops: dict | NoneType) -> tuple[list[str], list[str], list[str]]:
        post_cids = []
        repost_cids = []
        like_cids = []
        if ops is not None:
            for op in ops:
                action = op.get("action")
                match action:
                    case "create":
                        path = op.get("path")
                        match path:
                            case "app.bsky.feed.post":
                                post_cids.append(op.get("cid"))
                            case "app.bsky.feed.repost":
                                repost_cids.append(op.get("cid"))
                            case "app.bsky.feed.like":
                                like_cids.append(op.get("cid"))
                            case _:
                                logging.warning(f"Unhandled path encountered {path}")
                    case "delete":
                        logging.warning(f"Deletion action encountered: {op}")
                        # TODO: Implement deletions - probably _cids lists become lists of dicts not strings, with keys cid and action
                    case _:
                        logging.warning(f"Unhandled action encountered {action}")
        return post_cids, repost_cids, like_cids

    def _process_post(self, cids: list, body: dict) -> None:
        repo = body.get("repo")
        commit = body.get("commit")
        for cid in cids:
            storage_frame = {
                "cid": cid,
                "did": repo,
                "commit": commit,
                "created_at": "",
                "text": "",
                "langs": "",
                "facets": "",
                "has_embed": False,
                "embed_type": "",
                "embed_refs": [],
                "external_uri": "",
                "has_record": False,
                "record_cid": "",
                "record_uri": "",
                "is_reply": False,
                "reply_root_cid": "",
                "reply_root_uri": "",
                "reply_parent_cid": "",
                "reply_parent_uri": "",
            }
            # TODO: Do records include graph.list do I need to handle differently
            for block in body["blocks"].get("blocks"):
                if not isinstance(block, str) and cid == block.get("cid"):
                    storage_frame["created_at"] = block["data"].get("createdAt")
                    storage_frame["text"] = block["data"].get("text")
                    storage_frame["langs"] = block["data"].get("langs")
                    storage_frame["facets"] = json.dumps(block["data"].get("facets"), default=None)

                    storage_frame = self._process_embeds(block["data"], storage_frame)
                    if block["data"].get("reply") is not None:
                        storage_frame = self._process_replies(block["data"]["reply"], storage_frame)

                    self._wisp._insert_post(storage_frame)

    def _process_repost(self, cids: list, body: dict) -> None:
        repo = body.get("repo")
        commit = body.get("commit")
        try:
            for cid in cids:
                storage_frame = {"cid": cid, "did": repo, "commit": commit}
                for block in body["blocks"].get("blocks"):
                    if not isinstance(block, str) and cid == block.get("cid"):
                        storage_frame["subject_cid"] = block.get("data").get("subject").get("cid")
                        storage_frame["subject_uri"] = block.get("data").get("subject").get("uri")
                        storage_frame["created_at"] = block["data"].get("createdAt")
                        self._wisp._insert_repost(storage_frame)
        except AttributeError as e:
            logging.warning(f"Issue when processing block: {e}")
            logging.debug(body["blocks"].get("blocks"))
        except Exception as e:
            logging.exception(f"Exception in process repost {e} \n with data {body} ")

    def _process_like(self, cids: list, body: dict) -> None:
        repo = body.get("repo")
        commit = body.get("commit")
        try:
            for cid in cids:
                storage_frame = {"cid": cid, "did": repo, "commit": commit}
                for block in body["blocks"].get("blocks"):
                    if not isinstance(block, str) and cid == block.get("cid"):
                        storage_frame["subject_cid"] = block.get("data").get("subject").get("cid")
                        storage_frame["subject_uri"] = block.get("data").get("subject").get("uri")
                        storage_frame["created_at"] = block["data"].get("createdAt")
                        self._wisp._insert_like(storage_frame)
        except AttributeError as e:
            logging.warning(f"Issue when processing like: {e}")
            logging.warning(body["blocks"].get("blocks"))

    def _process_embeds(self, data_block: dict, storage_frame: dict) -> dict:
        # TODO: images#main, selectionQuote, secret, ""
        if data_block.get("embed") is not None:
            storage_frame["embed_type"] = data_block["embed"].get("$type")
            embed_type = str(storage_frame["embed_type"]).split(".")[-1]

            match embed_type:
                case "video":
                    storage_frame["has_embed"] = True
                    storage_frame["embed_refs"] = [(data_block["embed"].get(embed_type).get("ref"))]
                case "images":
                    storage_frame["has_embed"] = True
                    image_refs = []
                    for images in data_block["embed"].get(embed_type):
                        image_refs.append(images.get("image").get("ref"))
                    storage_frame["embed_refs"] = image_refs
                case "external":
                    storage_frame["has_embed"] = True
                    storage_frame["external_uri"] = [(data_block["embed"].get(embed_type).get("uri"))]
                case "record":
                    storage_frame["has_embed"] = False
                    storage_frame["has_record"] = True
                    storage_frame["record_cid"] = [(data_block["embed"].get(embed_type).get("cid"))]
                    storage_frame["record_uri"] = data_block["embed"].get(embed_type).get("uri")
                case "":
                    pass
                case "recordWithMedia":
                    storage_frame["has_embed"] = True
                    storage_frame["has_record"] = True
                    storage_frame["record_cid"] = [(data_block["embed"].get("record").get("record").get("cid"))]
                    storage_frame["record_uri"] = data_block["embed"].get("record").get("record").get("uri")
                    media_type = str(data_block["embed"].get("media").get("$type")).split(".")[-1]
                    storage_frame["embed_type"] = media_type
                    media_block = data_block["embed"].get("media")
                    match media_type:
                        case "video":
                            storage_frame["embed_refs"] = [(media_block.get(media_type).get("ref"))]

                        case "images":
                            image_refs = []
                            for images in media_block.get(media_type):
                                image_refs.append(images.get("image").get("ref"))
                            storage_frame["embed_refs"] = image_refs

                        case "external":
                            storage_frame["external_uri"] = [(media_block.get("external").get("uri"))]
                        case _:
                            logging.warning(f"media type not implemented {media_type}")
                            logging.debug(media_block)
                case _:
                    logging.warning(f"embed type not implemented {embed_type}")
                    logging.debug(data_block)
        return storage_frame

    def _process_replies(self, reply_block: dict, storage_frame: dict) -> dict:
        storage_frame["is_reply"] = True
        storage_frame["reply_root_cid"] = reply_block["root"].get("cid")
        storage_frame["reply_root_uri"] = reply_block["root"].get("uri")
        storage_frame["reply_parent_cid"] = reply_block["parent"].get("cid")
        storage_frame["reply_parent_uri"] = reply_block["parent"].get("uri")
        return storage_frame
