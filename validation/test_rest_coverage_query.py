import sqlite3
import unittest

from scripts.query_rest import _coverage_rows


class RestCoverageQueryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE rest_endpoints (id INTEGER PRIMARY KEY, method TEXT, path TEXT,
                entity_id INTEGER, source_version TEXT);
            CREATE TABLE repos (id INTEGER PRIMARY KEY, repo_key TEXT);
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE test_cases (id INTEGER PRIMARY KEY, repo_id INTEGER, file_id INTEGER,
                case_name TEXT, scenario_name TEXT, example_row TEXT, eligibility TEXT, jira_refs_json TEXT);
            CREATE TABLE test_requests (id INTEGER PRIMARY KEY, test_case_id INTEGER, step_line INTEGER,
                request_version TEXT, expected_status INTEGER, operation_kind TEXT);
            CREATE TABLE test_endpoint_links (id INTEGER PRIMARY KEY, test_request_id INTEGER,
                rest_endpoint_id INTEGER, compatibility_id INTEGER, resolution_kind TEXT);
            CREATE TABLE test_entity_links (id INTEGER PRIMARY KEY, test_request_id INTEGER,
                entity_id INTEGER, rest_endpoint_id INTEGER);
            CREATE TABLE api_version_compatibility (id INTEGER PRIMARY KEY, test_version TEXT,
                endpoint_version TEXT, status TEXT);
            CREATE TABLE test_diagnostics (id INTEGER PRIMARY KEY, repo_id INTEGER, file_id INTEGER,
                test_case_id INTEGER, test_request_id INTEGER, kind TEXT, message TEXT, source_line INTEGER);
            """
        )
        self.conn.executescript(
            """
            INSERT INTO rest_endpoints VALUES (1, 'GET', '/objects/ap-bill', 9, 's1');
            INSERT INTO rest_endpoints VALUES (2, 'POST', '/objects/ap-bill', 9, 's1');
            INSERT INTO repos VALUES (1, 'suite-a');
            INSERT INTO files VALUES (1, 'features/ap-bill.feature');
            INSERT INTO test_cases VALUES (1, 1, 1, 'Read bill', 'Read bill', NULL, 'active', '["JIRA-1"]');
            INSERT INTO test_cases VALUES (2, 1, 1, 'Create bill', 'Create bill', NULL, 'known_issue', '[]');
            INSERT INTO test_requests VALUES (1, 1, 10, 'v0', 200, 'item');
            INSERT INTO test_requests VALUES (2, 2, 20, 'v0', 201, 'collection');
            INSERT INTO test_endpoint_links VALUES (1, 1, 1, NULL, 'exact');
            INSERT INTO test_endpoint_links VALUES (2, 2, 2, NULL, 'exact');
            INSERT INTO test_entity_links VALUES (1, 1, 9, 1);
            INSERT INTO test_entity_links VALUES (2, 2, 9, 2);
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_only_active_cases_count_as_active_coverage(self):
        endpoints, diagnostics = _coverage_rows(self.conn, 9, 's1', 20)
        self.assertEqual([], diagnostics)
        self.assertEqual(['active', 'known_issue_only'], [item['coverage'] for item in endpoints])
        self.assertEqual(1, endpoints[0]['active_case_count'])
        self.assertEqual(1, endpoints[1]['known_issue_case_count'])

    def test_version_filter_uses_endpoint_source_version(self):
        endpoints, _ = _coverage_rows(self.conn, 9, 's2', 20)
        self.assertEqual([], endpoints)

    def test_version_filter_keeps_unresolved_endpoints_visible(self):
        self.conn.execute(
            "INSERT INTO rest_endpoints VALUES (3, 'DELETE', '/objects/ap-bill/3', 9, NULL)"
        )
        endpoints, _ = _coverage_rows(self.conn, 9, 's1', 20)
        self.assertEqual([3, 1, 2], [item['endpoint_id'] for item in endpoints])
        self.assertIsNone(endpoints[0]['source_version'])
