import os
import sys
from collections.abc import Sequence
from io import TextIOWrapper
from multiprocessing.connection import Connection

from sentry_sdk import start_transaction


def revert_worker(
    *,
    conn: Connection,
    changeset_ids: Sequence[int],
    comment: str,
    osm_token: str,
    discussion: str,
    discussion_target: str,
    print_osc: bool,
    query_filter: str,
    fix_parents: bool,
) -> int:
    # redirect stdout/stderr to the pipe
    sys.stdout = sys.stderr = TextIOWrapper(os.fdopen(conn.fileno(), 'wb', 0, closefd=False), write_through=True)

    from osm_revert.main import main

    with start_transaction(op='revert', name=revert_worker.__qualname__):
        return main(
            changeset_ids=changeset_ids,
            comment=comment,
            osm_token=osm_token,
            discussion=discussion,
            discussion_target=discussion_target,
            print_osc=print_osc,
            query_filter=query_filter,
            fix_parents=fix_parents,
        )
