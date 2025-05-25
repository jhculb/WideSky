# WideSky
## Description
A containerised Python based listener that ingests the Bluesky firehose and exports the processed data to a Postgres database to allow for easy collection of samples from BlueSky for research and wider purposes.

## Statement of Need
[BlueSky](https://bsky.app/) is an up-an-coming social media site based on the AT protocol, which may merit further study. However the ATProtocol can be difficult to interpret and code around, therefore this application is designed to ease the barrier of entry for bulk collection and analysis of BlueSky data.

## How to Use

1. Install Docker
2. Navigate to the root folder of the repository
3. Run '''docker compose up'''
4. Connect via your preferred method to the PostgreSQL database hosted locally at port 5432

*Please note:* that the bind mounted volumes are difficult to delete, due to security features within Docker. 
You will need to use a command such as `docker exec --privileged --user root <CONTAINER_ID> chown -R "$(id -u):$(id -g)" <TARGET_DIR>`, more details [here](https://stackoverflow.com/questions/42423999/cant-delete-file-created-via-docker).

*Please note:* Some instability has been observed when running WideSky for the first time. If the logs show connection errors between the Python and Postgres containers after building the project for the first time, please restart the application.

## Use Cases
This tool can be used to collect bulk bluesky data in a csv format, allowing for archiving and network, textual and other content analyses to be performed on the output data.

## Output
The schema for Postgres database is as follows:
### Users Table
| did              | first_known_as | also_known_as |
| ---------------- | -------------- | ------------- |
| TEXT PRIMARY KEY | TEXT           | TEXT          |
### Posts Table
| cid              | created_at               | did  | commit | text | langs      | facets | has_embed | embed_type | embed_refs | external_uri | has_record | record_cid | record_uri | is_reply | reply_root_cid | reply_root_uri | reply_parent_cid | reply_parent_uri |
| ---------------- | ------------------------ | ---- | ------ | ---- | ---------- | ------ | --------- | ---------- | ---------- | ------------ | ---------- | ---------- | ---------- | -------- | -------------- | -------------- | ---------------- | ---------------- |
| TEXT PRIMARY KEY | TIMESTAMP WITH TIME ZONE | TEXT | TEXT   | TEXT | TEXT ARRAY | JSONB  | BOOLEAN   | TEXT       | TEXT ARRAY | TEXT         | BOOLEAN    | TEXT       | TEXT       | BOOLEAN  | TEXT           | TEXT           | TEXT             | TEXT             |
### Likes Table
| cid              | created_at               | did  | commit | subject_cid | subject_url |
| ---------------- | ------------------------ | ---- | ------ | ----------- | ----------- |
| TEXT PRIMARY KEY | TIMESTAMP WITH TIME ZONE | TEXT | TEXT   | TEXT        | TEXT        |
### Reposts Table
| cid              | created_at               | did  | commit | subject_cid | subject_uri |
| ---------------- | ------------------------ | ---- | ------ | ----------- | ----------- |
| TEXT PRIMARY KEY | TIMESTAMP WITH TIME ZONE | TEXT | TEXT   | TEXT        | TEXT        |

## (Planned) Features
### Current Features
* Async functionality
* Exponential backoff for reconnections to firehose and reattempts for plc.directory
* Rotating Logging bind mounted to a widesky/logs folder
* Async workers for processing and batching to Postgres
* Batched Postgres saving

### To-do

* Implement graph.list post type
* Implement embed types
    * images#main
    * selectionQuote
    * secret
    * Others I have not seen?
* Improve error handling
* Add testing
* Capture PostgreSQL logs in logs/postgres
* Add webserver with metrics and ability to configure capture protocols
* Integrate with a crawler to reach back for full activity records of active users where not present already in data
* Add option to prevent HTTPX logging clogging up the logs
* Capture delete and other non-create events

## Acknowledgements
Thanks particularly to David Peck whose work I have captured in the firehose_utils.py file, who implemented a lovely decoding of the CBOR protocol. Please see his work here: https://gist.github.com/davepeck/8ada49d42d44a5632b540a4093225719 and https://github.com/davepeck.

## How to Cite
A technical paper will be released soon, for now please mention the github repository and in academic works please mention my ORCID (https://orcid.org/0009-0000-1581-4021).

## License
This work is licensed under the LGPL-3.0.

## Contact Details
In case of questions please contact [@jhculb](https://github.com/jhculb).

For contributions and bug reports, open an issue [here](https://github.com/jhculb/WideSky/issues).