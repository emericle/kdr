"""Keep ``schema.sql`` honest about the schema the code actually creates.

``DatabaseConnection.init_db()`` builds the tables from SQLAlchemy's
``Base.metadata``; ``schema.sql`` is the same schema rendered as SQL so it can be
handed to psql. Nothing loads that file at runtime, so a mismatch between the two
is invisible until someone bootstraps a database by hand. This test compares them
column by column, and checks the file is loadable rather than full of psql
directives that name a machine's paths.
"""
import re
from pathlib import Path

from src.database import Base

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "schema.sql"

# SQLAlchemy type -> the SQL type name schema.sql must use for it. ``DateTime``
# is checked separately because the timezone flag changes the SQL it compiles to.
SQL_TYPE_NAMES = {
    "Integer": "INTEGER",
    "String": "VARCHAR",
    "Float": "DOUBLE PRECISION",
    "Text": "TEXT",
}
# ``DateTime(timezone=True)`` compiles to this in the PostgreSQL dialect; a plain
# TIMESTAMP would store a naive value and lose the offset.
AWARE_TIMESTAMP = "TIMESTAMP WITH TIME ZONE"
NAIVE_TIMESTAMP = "TIMESTAMP"

CREATE_TABLE = re.compile(r"CREATE TABLE (\w+) \((.*?)\);", re.S)
COLUMN_LINE = re.compile(r"(\w+)\s+(.+?)\s*,?\s*$")
CREATE_INDEX = re.compile(r"CREATE (UNIQUE )?INDEX (\w+) ON (\w+) \(([^)]*)\);")


def sql_body() -> str:
    """The file with comment lines removed, so prose cannot satisfy a check."""
    return "\n".join(
        line for line in SCHEMA_SQL.read_text().splitlines() if not line.lstrip().startswith("--")
    )


def parse_tables(text: str) -> dict:
    """Return ``{table: [(column, spec), ...]}`` in declaration order."""
    tables = {}
    for name, body in CREATE_TABLE.findall(text):
        columns = []
        for raw in body.split(","):
            line = raw.strip()
            if not line:
                continue
            match = COLUMN_LINE.match(line)
            assert match, f"unparseable column line {line!r}"
            columns.append((match.group(1), match.group(2).upper()))
        tables[name] = columns
    return tables


def parse_indexes(text: str) -> dict:
    """Return ``{table: {column, ...}}`` for every indexed column."""
    indexes: dict[str, set[str]] = {}
    for _, _, table, columns in CREATE_INDEX.findall(text):
        for column in columns.split(","):
            indexes.setdefault(table, set()).add(column.strip())
    return indexes


def parse_unique_indexes(text: str) -> dict:
    """Return ``{table: (column, ...)}`` for every ``CREATE UNIQUE INDEX``."""
    return {
        table: tuple(column.strip() for column in columns.split(","))
        for unique, _, table, columns in CREATE_INDEX.findall(text)
        if unique
    }


class TestSchemaSqlMatchesModels:
    """The reference SQL file describes the tables SQLAlchemy will create."""

    def test_tables_match(self):
        """Every model table is declared, and nothing extra is left over."""
        declared = set(parse_tables(sql_body()))
        assert declared == set(Base.metadata.tables), (declared, set(Base.metadata.tables))

    def test_columns_match(self):
        """Columns match by name and order, so a hand-built table is not
        silently reordered against the model."""
        tables = parse_tables(sql_body())
        for name, model in Base.metadata.tables.items():
            assert [c.name for c in model.columns] == [c for c, _ in tables[name]], name

    def test_sql_types_match_models(self):
        """Each column's SQL type is the one its SQLAlchemy type maps to.

        ``Column(Float)`` is sent to the database as an IEEE-754 double, so the
        reference file must say DOUBLE PRECISION; the bare ``FLOAT`` alias would
        have rounded prices and volumes to single precision on a MySQL-style
        backend. ``DateTime(timezone=True)`` must say WITH TIME ZONE, which is
        what the PostgreSQL dialect emits for it.
        """
        tables = parse_tables(sql_body())
        for name, model in Base.metadata.tables.items():
            for column_name, spec in tables[name]:
                column_type = model.c[column_name].type
                if type(column_type).__name__ == "DateTime":
                    expected = AWARE_TIMESTAMP if column_type.timezone else NAIVE_TIMESTAMP
                else:
                    expected = SQL_TYPE_NAMES[type(column_type).__name__]
                assert spec == expected or spec.startswith(expected + " "), (
                    name,
                    column_name,
                    spec,
                    expected,
                )
        assert not re.search(r"\bFLOAT\b", sql_body(), re.I)

    def test_aware_timestamps_are_declared(self):
        """A timezone-aware model column is not rendered as a naive TIMESTAMP."""
        tables = parse_tables(sql_body())
        for name, model in Base.metadata.tables.items():
            for column_name, spec in tables[name]:
                column_type = model.c[column_name].type
                if type(column_type).__name__ == "DateTime" and column_type.timezone:
                    assert spec == AWARE_TIMESTAMP, (name, column_name, spec)

    def test_primary_key_matches(self):
        """PRIMARY KEY is on exactly the model's primary key columns."""
        tables = parse_tables(sql_body())
        for name, model in Base.metadata.tables.items():
            expected = {c.name for c in model.columns if c.primary_key}
            marked = {c for c, spec in tables[name] if "PRIMARY KEY" in spec}
            assert marked == expected, (name, marked, expected)

    def test_unique_columns_match(self):
        """``unique=True`` in the model shows up as UNIQUE in the SQL."""
        tables = parse_tables(sql_body())
        for name, model in Base.metadata.tables.items():
            expected = {c.name for c in model.columns if c.unique}
            marked = {c for c, spec in tables[name] if "UNIQUE" in spec}
            assert marked == expected, name

    def test_indexed_columns_match(self):
        """Every indexed column has an index, and no index names a phantom."""
        indexed = {
            name: {c.name for c in model.columns if c.index}
            for name, model in Base.metadata.tables.items()
            if {c.name for c in model.columns if c.index}
        }
        assert parse_indexes(sql_body()) == indexed

    def test_unique_indexes_match_models(self):
        """A composite ``Index(..., unique=True)`` is a ``CREATE UNIQUE INDEX``.

        ``market_data`` carries one on ``(symbol, timestamp)``, which is what
        lets ``add_market_data`` skip a bar a stream reconnect handed back. A
        composite index is not covered by the per-column check above, so the
        uniqueness it buys would be missing from a hand-bootstrapped database.
        """
        expected = {
            name: tuple(column.name for column in index.columns)
            for name, table in Base.metadata.tables.items()
            for index in table.indexes
            if index.unique and len(index.columns) > 1
        }
        declared = parse_unique_indexes(sql_body())
        assert declared == expected, (declared, expected)


class TestSchemaSqlLoadable:
    """The file can actually be handed to psql."""

    def test_no_psql_include_directives(self):
        """No ``\\i 'x' TO y`` lines pointing at one machine's absolute paths."""
        for line in SCHEMA_SQL.read_text().splitlines():
            assert not line.lstrip().startswith("\\"), line

    def test_index_syntax_is_portable(self):
        """Indexes use ``ON table (column)``, not the ``TABLE x (y)`` form."""
        for line in sql_body().splitlines():
            stripped = line.lstrip().upper()
            if stripped.startswith("CREATE INDEX") or stripped.startswith("CREATE UNIQUE INDEX"):
                assert " ON " in line, line

    def test_every_statement_is_terminated(self):
        """Each declaration ends with a semicolon so psql can read the file."""
        body = sql_body()
        declarations = len(CREATE_TABLE.findall(body)) + len(CREATE_INDEX.findall(body))
        assert body.count(";") == declarations, (body.count(";"), declarations)
