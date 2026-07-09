# parser/extractors/cqry_extractor.py
# Dedicated extractor for .cqry files (query definition files)
#
# .cqry files contain PHP arrays of query definitions.
# This extractor focuses on extracting query definitions and their metadata.
# Structure: $k<EntityName>Queries['QRY_<QUERY_NAME>'] = array('QUERY' => ..., 'ARGTYPES' => ...)
# This path isolates .cqry-specific logic from the standard PHP extractor.

from __future__ import annotations

import re
from .base import Symbol


def extract(source: bytes) -> list[Symbol]:
    """
    Extract symbols from .cqry files.

    Returns symbols of kinds:
    - cqry_query: Each unique query definition (QRY_XXXXX)
    - cqry_table: SQL table names found in queries
    - cqry_field: SQL field/column references in queries
    - cqry_join: JOIN clauses (if present)
    """
    text = source.decode("utf-8", errors="replace")
    symbols: list[Symbol] = []

    # Extract all query definitions in the file
    # Pattern: ['QRY_NAME'] = array (
    query_pattern = r"\['(QRY_[A-Z0-9_]+)'\]\s*=\s*array\s*\("
    query_matches = re.finditer(query_pattern, text)

    for query_match in query_matches:
        query_name = query_match.group(1)
        query_line = text[: query_match.start()].count("\n") + 1

        # Create symbol for the query definition
        symbols.append(
            Symbol(
                name=query_name,
                kind="cqry_query",
                language="php",
                start_line=query_line,
                end_line=query_line,
                parent_symbol=None,
                signature=f"array(QUERY, ARGTYPES)",
            )
        )

        # Extract SQL queries and parse them for tables/fields
        query_text_start = query_match.end()
        query_end_pattern = r"\)\s*[,;]"
        query_end_match = re.search(
            query_end_pattern, text[query_text_start : query_text_start + 2000]
        )

        if query_end_match:
            query_definition = text[
                query_text_start : query_text_start + query_end_match.start()
            ]

            # Extract SQL query string
            sql_pattern = r"['\"]QUERY['\"]?\s*=>\s*['\"]([^'\"]*?)['\"]"
            sql_match = re.search(sql_pattern, query_definition)

            if sql_match:
                sql_query = sql_match.group(1)

                # Extract table names (after FROM, JOIN, etc.)
                table_pattern = (
                    r"(?:FROM|JOIN|INTO|UPDATE|DELETE FROM)\s+([A-Z_][A-Za-z0-9_#]*)"
                )
                table_matches = re.finditer(table_pattern, sql_query, re.IGNORECASE)

                for table_match in table_matches:
                    table_name = table_match.group(1)
                    table_line = query_line + sql_query[: table_match.start()].count(
                        "\n"
                    )

                    symbols.append(
                        Symbol(
                            name=table_name,
                            kind="cqry_table",
                            language="sql",
                            start_line=table_line,
                            end_line=table_line,
                            parent_symbol=query_name,
                            signature=f"table reference in query",
                        )
                    )

                # Extract JOIN clauses
                join_pattern = r"((?:INNER\s+|LEFT\s+|RIGHT\s+|FULL\s+|CROSS\s+)?JOIN\s+\w+[^,]*?(?:ON|USING)[^,]*)"
                join_matches = re.finditer(join_pattern, sql_query, re.IGNORECASE)

                for join_match in join_matches:
                    join_clause = join_match.group(1).strip()
                    join_line = query_line + sql_query[: join_match.start()].count("\n")

                    symbols.append(
                        Symbol(
                            name=join_clause[:50],  # Truncate for readability
                            kind="cqry_join",
                            language="sql",
                            start_line=join_line,
                            end_line=join_line,
                            parent_symbol=query_name,
                            signature=join_clause,
                        )
                    )

                # Extract field/column references (simplified: words after SELECT, WHERE, etc.)
                field_pattern = (
                    r"(?:SELECT|WHERE|AND|OR|ON)\s+([A-Za-z_#][A-Za-z0-9_#.]*)"
                )
                field_matches = re.finditer(field_pattern, sql_query, re.IGNORECASE)

                for field_match in field_matches:
                    field_name = field_match.group(1)
                    # Skip common SQL functions and keywords
                    if field_name.upper() not in (
                        "COUNT",
                        "MAX",
                        "MIN",
                        "SUM",
                        "AVG",
                        "DISTINCT",
                        "NOT",
                        "IN",
                        "EXISTS",
                        "SELECT",
                    ):
                        field_line = query_line + sql_query[
                            : field_match.start()
                        ].count("\n")

                        symbols.append(
                            Symbol(
                                name=field_name,
                                kind="cqry_field",
                                language="sql",
                                start_line=field_line,
                                end_line=field_line,
                                parent_symbol=query_name,
                                signature=f"field/column reference",
                            )
                        )

    return symbols
