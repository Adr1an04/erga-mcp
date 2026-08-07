from __future__ import annotations

import io
import json
import socket
import time
import unittest
from datetime import UTC, datetime
from threading import Event, Thread
from typing import Any
from unittest.mock import MagicMock, call, patch

from erga_mcp.job_intake import (
    _validate_public_job_url,
    fetch_job_snapshot,
    select_relevant_evidence,
)
from erga_mcp.models import Evidence


class JobIntakeTests(unittest.TestCase):
    @staticmethod
    def _socket_with_response(response: bytes) -> MagicMock:
        network_socket = MagicMock()
        network_socket.makefile.return_value = io.BytesIO(response)
        return network_socket

    @staticmethod
    def _public_resolution(
        address: str,
        port: int,
        family: int = socket.AF_INET,
    ) -> list[tuple[object, ...]]:
        socket_address: tuple[object, ...]
        if family == socket.AF_INET6:
            socket_address = (address, port, 0, 0)
        else:
            socket_address = (address, port)
        return [
            (
                family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                socket_address,
            )
        ]

    def test_recovers_shopify_hydration_job_text_from_a_careers_detail_page(self) -> None:
        description = (
            "Being a Shopify Intern\\n\\nAbout the role\\n\\n"
            "Applied Machine Learning Interns build models at scale.\\n\\n"
            "Qualifications:\\n\\n - You love solving tough problems with code.\\n\\n"
            "Compensation:\\n\\nPaid internship.\\n\\nApply Now"
        )
        page = (
            f"<html><body><main>Back</main><script>{json.dumps(description)}</script></body></html>"
        )
        url = "https://www.shopify.com/careers/applied-machine-learning-internships-winter-2027_example"

        with patch("erga_mcp.job_intake.fetch_public_page", return_value=page):
            snapshot = fetch_job_snapshot(url)

        self.assertIn("Applied Machine Learning Interns", snapshot)

    def test_rejects_credentials_and_private_network_destinations(self) -> None:
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            _validate_public_job_url("https://user:password@jobs.example.test/role")

        private_resolution = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with (
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                return_value=private_resolution,
            ),
            self.assertRaisesRegex(ValueError, "public network addresses"),
        ):
            _validate_public_job_url("https://jobs.example.test/role")

    def test_fetch_connects_to_the_validated_numeric_ip(self) -> None:
        body = b"<html><body><h1>Software Intern</h1><p>Build systems.</p></body></html>"
        network_socket = self._socket_with_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        resolver = MagicMock(return_value=self._public_resolution("93.184.216.34", 80))

        with (
            patch("erga_mcp.job_intake.socket.getaddrinfo", resolver),
            patch("erga_mcp.job_intake.socket.socket", return_value=network_socket),
        ):
            snapshot = fetch_job_snapshot("http://jobs.example.test/role")

        self.assertEqual(snapshot, "Software Intern Build systems.")
        resolver.assert_called_once_with("jobs.example.test", 80, type=socket.SOCK_STREAM)
        network_socket.connect.assert_called_once_with(("93.184.216.34", 80))

    def test_fetch_snapshot_excludes_page_scripts_but_preserves_job_metadata(self) -> None:
        body = b"""
        <html><head>
        <script>const React = 'JavaScript'; const legal = 'laws trust expressed';</script>
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Systems Intern",
         "description":"Build C++ services.",
         "hiringOrganization":{"name":"Example"}}
        </script></head>
        <body><main><h1>Systems Intern</h1><p>Build C++ services.</p></main></body></html>
        """
        network_socket = self._socket_with_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        with (
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                return_value=self._public_resolution("93.184.216.34", 80),
            ),
            patch(
                "erga_mcp.job_intake.socket.socket",
                return_value=network_socket,
            ),
        ):
            snapshot = fetch_job_snapshot("http://jobs.example.test/role")

        self.assertIn("Systems Intern Build C++ services.", snapshot)
        self.assertIn('"@type": "JobPosting"', snapshot)
        for contamination in ("React", "JavaScript", "laws", "trust", "expressed"):
            self.assertNotIn(contamination, snapshot)

    def test_fetch_falls_back_to_each_validated_endpoint(self) -> None:
        unreachable_socket = MagicMock()
        unreachable_socket.connect.side_effect = OSError("IPv6 route unavailable")
        body = b"<p>Fallback role</p>"
        working_socket = self._socket_with_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        resolution = self._public_resolution(
            "2606:4700:4700::1111",
            80,
            socket.AF_INET6,
        ) + self._public_resolution("93.184.216.34", 80)
        socket_constructor = MagicMock(side_effect=[unreachable_socket, working_socket])

        with (
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                return_value=resolution,
            ),
            patch("erga_mcp.job_intake.socket.socket", socket_constructor),
        ):
            snapshot = fetch_job_snapshot("http://jobs.example.test/role")

        self.assertEqual(snapshot, "Fallback role")
        unreachable_socket.connect.assert_called_once_with(("2606:4700:4700::1111", 80, 0, 0))
        unreachable_socket.close.assert_called_once_with()
        working_socket.connect.assert_called_once_with(("93.184.216.34", 80))
        self.assertEqual(
            socket_constructor.call_args_list,
            [
                call(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP),
                call(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP),
            ],
        )

    def test_https_pins_the_ip_but_authenticates_the_original_hostname(self) -> None:
        body = b"<p>Secure role</p>"
        network_socket = self._socket_with_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        tls_context = MagicMock()
        tls_context.wrap_socket.return_value = network_socket

        with (
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                return_value=self._public_resolution("93.184.216.34", 443),
            ),
            patch("erga_mcp.job_intake.socket.socket", return_value=network_socket),
            patch(
                "erga_mcp.job_intake.ssl.create_default_context",
                return_value=tls_context,
            ) as create_default_context,
        ):
            snapshot = fetch_job_snapshot("https://jobs.example.test/role")

        self.assertEqual(snapshot, "Secure role")
        network_socket.connect.assert_called_once_with(("93.184.216.34", 443))
        create_default_context.assert_called_once_with()
        tls_context.wrap_socket.assert_called_once_with(
            network_socket,
            server_hostname="jobs.example.test",
        )

    def test_redirect_revalidates_and_pins_the_new_hostname(self) -> None:
        first_socket = self._socket_with_response(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: http://careers.example.test/final\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n\r\n"
        )
        body = b"<p>Redirected role</p>"
        second_socket = self._socket_with_response(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        resolutions = {
            "jobs.example.test": self._public_resolution("93.184.216.34", 80),
            "careers.example.test": self._public_resolution("142.250.72.14", 80),
        }

        def resolve(hostname: str, port: int, *, type: int) -> list[tuple[object, ...]]:
            self.assertEqual(type, socket.SOCK_STREAM)
            self.assertEqual(port, 80)
            return resolutions[hostname]

        with (
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                side_effect=resolve,
            ) as resolver,
            patch(
                "erga_mcp.job_intake.socket.socket",
                side_effect=[first_socket, second_socket],
            ),
        ):
            snapshot = fetch_job_snapshot("http://jobs.example.test/start")

        self.assertEqual(snapshot, "Redirected role")
        self.assertEqual(
            resolver.call_args_list,
            [
                call("jobs.example.test", 80, type=socket.SOCK_STREAM),
                call("careers.example.test", 80, type=socket.SOCK_STREAM),
            ],
        )
        first_socket.connect.assert_called_once_with(("93.184.216.34", 80))
        second_socket.connect.assert_called_once_with(("142.250.72.14", 80))

    def test_uses_one_deadline_across_dns_redirects_connects_and_reads(self) -> None:
        now = [100.0]

        class AdvancingBody(io.BytesIO):
            def read(self, size: int | None = -1) -> bytes:
                now[0] += 11
                return super().read(size)

        first_socket = self._socket_with_response(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: http://careers.example.test/final\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n\r\n"
        )

        def spend_ten_seconds(_: tuple[object, ...]) -> None:
            now[0] += 10

        first_socket.connect.side_effect = spend_ten_seconds
        body = b"<p>Deadline role</p>"
        second_socket = MagicMock()
        second_socket.makefile.return_value = AdvancingBody(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )

        def resolve(hostname: str, port: int, *, type: int) -> list[tuple[object, ...]]:
            del hostname, type
            now[0] += 5
            return self._public_resolution("93.184.216.34", port)

        with (
            patch("erga_mcp.job_intake.time.monotonic", side_effect=lambda: now[0]),
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                side_effect=resolve,
            ) as resolver,
            patch(
                "erga_mcp.job_intake.socket.socket",
                side_effect=[first_socket, second_socket],
            ),
            self.assertRaisesRegex(TimeoutError, "30 second deadline"),
        ):
            fetch_job_snapshot("http://jobs.example.test/start")

        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(first_socket.settimeout.call_args_list[0], call(25.0))
        self.assertEqual(second_socket.settimeout.call_args_list[0], call(10.0))

    def test_hard_deadline_interrupts_a_blocking_body_with_daemon_workers(self) -> None:
        entered_body_read = Event()
        released = Event()

        class BlockingBody(io.BytesIO):
            def read(self, size: int | None = -1) -> bytes:
                entered_body_read.set()
                released.wait(timeout=1)
                return super().read(size)

        body = b"<p>Slow role</p>"
        network_socket = MagicMock()
        network_socket.makefile.return_value = BlockingBody(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )
        network_socket.shutdown.side_effect = lambda _: released.set()
        network_socket.close.side_effect = released.set
        created_threads: list[Thread] = []

        def create_thread(*args: Any, **kwargs: Any) -> Thread:
            worker = Thread(*args, **kwargs)
            created_threads.append(worker)
            return worker

        started_at = time.perf_counter()
        with (
            patch("erga_mcp.job_intake._JOB_FETCH_TIMEOUT_SECONDS", 0.05),
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                return_value=self._public_resolution("93.184.216.34", 80),
            ),
            patch("erga_mcp.job_intake.socket.socket", return_value=network_socket),
            patch("erga_mcp.job_intake.Thread", side_effect=create_thread),
            self.assertRaisesRegex(TimeoutError, "30 second deadline"),
        ):
            fetch_job_snapshot("http://jobs.example.test/slow")
        elapsed = time.perf_counter() - started_at

        for worker in created_threads:
            worker.join(timeout=0.2)
        self.assertLess(elapsed, 0.3)
        self.assertTrue(entered_body_read.is_set())
        self.assertTrue(created_threads)
        self.assertTrue(all(worker.daemon for worker in created_threads))
        self.assertTrue(all(not worker.is_alive() for worker in created_threads))
        network_socket.shutdown.assert_called_with(socket.SHUT_RDWR)

    def test_redirect_to_a_private_address_is_rejected_before_connecting(self) -> None:
        first_socket = self._socket_with_response(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: http://internal.example.test/secret\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n\r\n"
        )

        def resolve(hostname: str, port: int, *, type: int) -> list[tuple[object, ...]]:
            del type
            if hostname == "jobs.example.test":
                return self._public_resolution("93.184.216.34", port)
            return self._public_resolution("127.0.0.1", port)

        socket_constructor = MagicMock(return_value=first_socket)
        with (
            patch("erga_mcp.job_intake.socket.getaddrinfo", side_effect=resolve),
            patch("erga_mcp.job_intake.socket.socket", socket_constructor),
            self.assertRaisesRegex(ValueError, "public network addresses"),
        ):
            fetch_job_snapshot("http://jobs.example.test/start")

        socket_constructor.assert_called_once()
        first_socket.connect.assert_called_once_with(("93.184.216.34", 80))

    def test_follows_no_more_than_five_redirects(self) -> None:
        redirect_sockets = [
            self._socket_with_response(
                b"HTTP/1.1 302 Found\r\n"
                + f"Location: /step-{index + 1}\r\n".encode()
                + b"Content-Length: 0\r\n"
                + b"Connection: close\r\n\r\n"
            )
            for index in range(6)
        ]
        socket_constructor = MagicMock(side_effect=redirect_sockets)
        with (
            patch(
                "erga_mcp.job_intake.socket.getaddrinfo",
                return_value=self._public_resolution("93.184.216.34", 80),
            ) as resolver,
            patch("erga_mcp.job_intake.socket.socket", socket_constructor),
            self.assertRaisesRegex(ValueError, "5 redirect limit"),
        ):
            fetch_job_snapshot("http://jobs.example.test/start")

        self.assertEqual(resolver.call_count, 6)
        self.assertEqual(socket_constructor.call_count, 6)
        for network_socket in redirect_sockets:
            network_socket.connect.assert_called_once_with(("93.184.216.34", 80))

    def test_rejects_unsupported_or_oversized_responses(self) -> None:
        cases = (
            (
                b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                "HTML, plain text, or JSON",
            ),
            (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/octet-stream\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n",
                "HTML, plain text, or JSON",
            ),
            (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/html\r\n"
                b"Content-Length: 2097153\r\n"
                b"Connection: close\r\n\r\n",
                "2 MiB snapshot limit",
            ),
        )
        for response, error_pattern in cases:
            with self.subTest(error_pattern=error_pattern):
                network_socket = self._socket_with_response(response)
                with (
                    patch(
                        "erga_mcp.job_intake.socket.getaddrinfo",
                        return_value=self._public_resolution("93.184.216.34", 80),
                    ),
                    patch(
                        "erga_mcp.job_intake.socket.socket",
                        return_value=network_socket,
                    ),
                    self.assertRaisesRegex(ValueError, error_pattern),
                ):
                    fetch_job_snapshot("http://jobs.example.test/role")

    def test_selects_only_approved_evidence_with_job_keyword_overlap(self) -> None:
        evidence = [
            Evidence(
                "ev1",
                "Career#Projects",
                "Built Python data pipelines for ML training.",
                True,
                datetime.now(UTC),
            ),
            Evidence(
                "ev2",
                "Career#Experience",
                "Led customer success renewals.",
                True,
                datetime.now(UTC),
            ),
            Evidence(
                "ev3", "Career#Private", "Unapproved Kubernetes work.", False, datetime.now(UTC)
            ),
        ]
        selected = select_relevant_evidence("Python machine learning engineer", evidence)
        self.assertEqual([item.id for item in selected], ["ev1"])


if __name__ == "__main__":
    unittest.main()
