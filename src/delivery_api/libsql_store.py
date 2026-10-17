"""Turso embedded-replica adapter with SQLite-compatible local semantics."""
from __future__ import annotations
from collections.abc import Sequence
from pathlib import Path
from typing import Any

class MappingRow(dict):
    def __init__(self,columns:Sequence[str],values:Sequence[Any]):
        super().__init__(zip(columns,values));self._values=tuple(values)
    def __getitem__(self,key):return self._values[key] if isinstance(key,int) else super().__getitem__(key)

class CursorAdapter:
    def __init__(self,cursor):self._cursor=cursor
    @property
    def lastrowid(self):return self._cursor.lastrowid
    def _row(self,row):
        if row is None:return None
        columns=[d[0] for d in self._cursor.description]
        return MappingRow(columns,row)
    def fetchone(self):return self._row(self._cursor.fetchone())
    def fetchall(self):return [self._row(row) for row in self._cursor.fetchall()]
    def __iter__(self):
        while True:
            row=self.fetchone()
            if row is None:return
            yield row

class ConnectionAdapter:
    """Expose sqlite3.Row-compatible results for every libSQL cursor."""
    def __init__(self,connection):self._connection=connection
    def execute(self,*args,**kwargs):return CursorAdapter(self._connection.execute(*args,**kwargs))
    def executemany(self,*args,**kwargs):return CursorAdapter(self._connection.executemany(*args,**kwargs))
    def executescript(self,*args,**kwargs):return self._connection.executescript(*args,**kwargs)
    def commit(self):return self._connection.commit()
    def sync(self):return self._connection.sync()


def normalize_connection(connection):
    """Keep tuple-shaped libSQL rows behind the storage adapter boundary."""
    return connection if isinstance(connection,ConnectionAdapter) else ConnectionAdapter(connection)


class ReplicaStateError(RuntimeError):
    """The local file cannot be opened as an embedded replica without its sync metadata."""


def connect_replica(path:Path,url:str,token:str,sync_interval:int=60):
    import libsql
    path.parent.mkdir(parents=True,exist_ok=True)
    metadata=Path(str(path)+"-info")
    # libSQL emits a native ERROR before raising for this state. Detect it in
    # Python first: a plain SQLite DB is not an embedded replica and must stay
    # available locally rather than producing a scary native failure line.
    if path.exists() and not metadata.exists():
        raise ReplicaStateError("the existing local SQLite database has no embedded-replica metadata; cloud sync is disabled until a separate replica is initialized")
    if metadata.exists() and not path.exists():
        raise ReplicaStateError("embedded-replica metadata exists without its database file; cloud sync is disabled until the replica pair is repaired")
    return normalize_connection(libsql.connect(str(path),sync_url=url,auth_token=token,sync_interval=sync_interval))
