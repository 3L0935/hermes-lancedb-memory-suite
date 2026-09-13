import json
import gzip
import http.client
import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lancedb_http_security_server", ROOT / "server" / "server.py"
)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class ServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.static_dir = self.root / "static"
        self.static_dir.mkdir()
        (self.static_dir / "index.html").write_text("safe index")
        (self.root / "sentinel.txt").write_text("audit-sentinel")
        self.previous_static_dir = server.STATIC_DIR
        server.STATIC_DIR = self.static_dir
        self.httpd = server.HTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server.STATIC_DIR = self.previous_static_dir
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_port, timeout=3
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), payload

    def test_static_parent_traversal_cannot_read_sentinel(self):
        status, _headers, body = self.request("GET", "/static/../sentinel.txt")

        self.assertEqual(404, status)
        self.assertNotIn(b"audit-sentinel", body)

    def test_static_symlink_cannot_escape_static_directory(self):
        (self.static_dir / "sentinel-link.txt").symlink_to(
            self.root / "sentinel.txt"
        )

        status, _headers, body = self.request(
            "GET", "/static/sentinel-link.txt"
        )

        self.assertEqual(404, status)
        self.assertNotIn(b"audit-sentinel", body)

    def test_regular_static_file_remains_available_without_cors(self):
        status, headers, body = self.request("GET", "/static/index.html")

        self.assertEqual(200, status)
        self.assertEqual(b"safe index", body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_gzip_negotiation_honors_explicit_zero_and_sets_real_length(self):
        payload = ("compressible javascript;" * 100).encode()
        (self.static_dir / "asset.js").write_bytes(payload)

        status, headers, body = self.request(
            "GET", "/static/asset.js", headers={"Accept-Encoding": "br, *;q=0.5"}
        )
        refused_status, refused_headers, refused_body = self.request(
            "GET", "/static/asset.js",
            headers={"Accept-Encoding": "*;q=1, gzip;q=0"},
        )

        self.assertEqual(200, status)
        self.assertEqual("gzip", headers["Content-Encoding"])
        self.assertEqual("Accept-Encoding", headers["Vary"])
        self.assertEqual(len(body), int(headers["Content-Length"]))
        self.assertEqual(payload, gzip.decompress(body))
        self.assertEqual(200, refused_status)
        self.assertNotIn("Content-Encoding", refused_headers)
        self.assertEqual("Accept-Encoding", refused_headers["Vary"])
        self.assertEqual(payload, refused_body)

    def test_static_etag_revalidates_and_changes_with_file_content(self):
        asset = self.static_dir / "asset.js"
        asset.write_text("const version = 1;")

        status, headers, body = self.request("GET", "/static/asset.js")
        etag = headers["ETag"]
        unchanged_status, unchanged_headers, unchanged_body = self.request(
            "GET", "/static/asset.js", headers={"If-None-Match": f'W/{etag}, "other"'}
        )
        asset.write_text("const version = 2;")
        changed_status, changed_headers, changed_body = self.request(
            "GET", "/static/asset.js", headers={"If-None-Match": etag}
        )

        self.assertEqual(200, status)
        self.assertEqual(b"const version = 1;", body)
        self.assertEqual("private, no-cache", headers["Cache-Control"])
        self.assertEqual(304, unchanged_status)
        self.assertEqual(etag, unchanged_headers["ETag"])
        self.assertEqual(b"", unchanged_body)
        self.assertEqual(200, changed_status)
        self.assertNotEqual(etag, changed_headers["ETag"])
        self.assertEqual(b"const version = 2;", changed_body)

    def test_json_is_gzipped_but_not_persistently_cached(self):
        with patch.object(server, "get_stats", return_value={"values": list(range(100))}):
            status, headers, body = self.request(
                "GET", "/api/stats", headers={"Accept-Encoding": "gzip"}
            )

        self.assertEqual(200, status)
        self.assertEqual("gzip", headers["Content-Encoding"])
        self.assertEqual("Accept-Encoding", headers["Vary"])
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual(len(body), int(headers["Content-Length"]))
        self.assertEqual({"values": list(range(100))}, json.loads(gzip.decompress(body)))

    def test_post_rejects_non_local_host(self):
        status, _headers, body = self.request(
            "POST",
            "/api/not-found",
            body=b"{}",
            headers={"Host": "attacker.example", "Content-Type": "application/json"},
        )

        self.assertEqual(403, status)
        self.assertIn(b"Local same-origin request required", body)

    def test_post_rejects_cross_origin_request(self):
        status, _headers, body = self.request(
            "POST",
            "/api/not-found",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://attacker.example",
            },
        )

        self.assertEqual(403, status)
        self.assertIn(b"Local same-origin request required", body)

    def test_post_accepts_same_origin_local_json(self):
        authority = f"127.0.0.1:{self.httpd.server_port}"
        status, headers, body = self.request(
            "POST",
            "/api/not-found",
            body=b"{}",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Origin": f"http://{authority}",
            },
        )

        self.assertEqual(404, status)
        self.assertEqual({"error": "Not found"}, json.loads(body))
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_post_rejects_non_json_body(self):
        status, _headers, body = self.request(
            "POST",
            "/api/not-found",
            body=b"memory_id=aaaaaaaa-aaa",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(415, status)
        self.assertIn(b"Content-Type must be application/json", body)

    def test_post_rejects_body_over_size_limit_without_reading_it(self):
        status, _headers, body = self.request(
            "POST",
            "/api/not-found",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(server.MAX_JSON_BODY_BYTES + 1),
            },
        )

        self.assertEqual(413, status)
        self.assertIn(b"JSON body exceeds 2 MiB limit", body)

    def test_post_rejects_malformed_or_non_object_json(self):
        malformed_status, _headers, _body = self.request(
            "POST",
            "/api/not-found",
            body=b"{",
            headers={"Content-Type": "application/json"},
        )
        array_status, _headers, _body = self.request(
            "POST",
            "/api/not-found",
            body=b"[]",
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(400, malformed_status)
        self.assertEqual(400, array_status)


class ConcurrentTransportTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def request(self, path):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_port, timeout=3
        )
        connection.request("GET", path)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), payload

    def test_small_route_completes_while_graph_request_is_blocked(self):
        graph_started = threading.Event()
        release_graph = threading.Event()
        graph_result = []

        def blocked_graph(**_kwargs):
            graph_started.set()
            self.assertTrue(release_graph.wait(timeout=2))
            return {"nodes": [], "edges": []}

        with patch.object(server, "get_graph_data", side_effect=blocked_graph), \
             patch.object(server, "get_stats", return_value={"total": 1}):
            worker = threading.Thread(
                target=lambda: graph_result.append(self.request("/api/graph"))
            )
            worker.start()
            self.assertTrue(graph_started.wait(timeout=2))
            status, _headers, body = self.request("/api/stats")
            release_graph.set()
            worker.join(timeout=2)

        self.assertEqual(200, status)
        self.assertEqual({"total": 1}, json.loads(body))
        self.assertEqual(200, graph_result[0][0])

    def test_third_heavy_request_is_rejected_while_two_are_running(self):
        both_started = threading.Barrier(3)
        release = threading.Event()
        results = []

        def blocked_graph(**_kwargs):
            both_started.wait(timeout=2)
            self.assertTrue(release.wait(timeout=2))
            return {"nodes": [], "edges": []}

        with patch.object(server, "get_graph_data", side_effect=blocked_graph):
            workers = [
                threading.Thread(
                    target=lambda: results.append(self.request("/api/graph"))
                )
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            both_started.wait(timeout=2)
            status, headers, body = self.request("/api/graph")
            release.set()
            for worker in workers:
                worker.join(timeout=2)

        self.assertEqual(503, status)
        self.assertEqual("1", headers["Retry-After"])
        self.assertEqual("heavy_work_busy", json.loads(body)["code"])
        self.assertEqual([200, 200], sorted(result[0] for result in results))

    def test_graph_default_matches_slider_and_th_alias_is_supported(self):
        seen = []

        def graph(**kwargs):
            seen.append(kwargs)
            return {"nodes": [], "edges": []}

        with patch.object(server, "get_graph_data", side_effect=graph):
            self.assertEqual(200, self.request("/api/graph")[0])
            self.assertEqual(200, self.request("/api/graph?th=0.7")[0])
            self.assertEqual(
                200, self.request("/api/graph?threshold=0.9&th=0.6")[0]
            )

        self.assertEqual([0.8, 0.7, 0.9], [call["threshold"] for call in seen])


class ContainerBindingTests(unittest.TestCase):
    def test_container_ports_are_published_on_ipv4_loopback_only(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        docker_run = (ROOT / "scripts" / "docker-run.sh").read_text()

        self.assertIn('"127.0.0.1:7777:7777"', compose)
        self.assertIn('-p "127.0.0.1:${PORT}:7777"', docker_run)
        self.assertNotIn('- "7777:7777"', compose)
        self.assertNotIn('-p "${PORT}:7777"', docker_run)


if __name__ == "__main__":
    unittest.main()
