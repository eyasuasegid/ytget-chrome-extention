#!/usr/bin/env python3
"""Unit tests for ytget_server.py"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import ytget_server
from ytget_server import (
    DownloadJob,
    JobManager,
    JobStatus,
    YtGetRequestHandler,
    QUALITY_CHOICES,
    format_size,
    format_duration,
    output_path,
    already_downloaded,
    is_single_video_url,
    format_selectors,
    youtube_js_opts,
    build_download_opts,
    sanitize_filename,
)


class TestCoreDownloadEngine(unittest.TestCase):
    def test_quality_choices_contains_audio(self):
        self.assertIn("audio", QUALITY_CHOICES)
        self.assertEqual(QUALITY_CHOICES["audio"], "audio")

    def test_is_single_video_url(self):
        self.assertTrue(is_single_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(is_single_video_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertTrue(is_single_video_url("https://www.youtube.com/shorts/dQw4w9WgXcQ"))
        self.assertTrue(is_single_video_url("https://www.youtube.com/live/dQw4w9WgXcQ"))
        self.assertTrue(is_single_video_url("https://www.youtube.com/embed/dQw4w9WgXcQ"))
        self.assertFalse(is_single_video_url("https://www.youtube.com/playlist?list=PL12345"))
        self.assertFalse(is_single_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL12345"))

    def test_format_selectors(self):
        sel_best = format_selectors(None)
        self.assertEqual(len(sel_best), 3)
        self.assertIn("bestvideo+bestaudio/best", sel_best[0])

        sel_720 = format_selectors(720)
        self.assertEqual(len(sel_720), 3)
        self.assertNotIn("bestvideo+bestaudio/best", sel_720[0])
        self.assertIn("height<=720", sel_720[0])

        sel_audio = format_selectors("audio")
        self.assertEqual(len(sel_audio), 3)
        self.assertIn("bestaudio/best", sel_audio[0])
        self.assertIn("ba/b", sel_audio[1])
        self.assertEqual(sel_audio[2], "best")

    def test_output_path(self):
        out_dir = Path("/test/downloads")
        p1 = output_path(out_dir, 5, 20, is_playlist=True)
        self.assertEqual(p1.name, "05 - %(title)s.%(ext)s")

        p2 = output_path(out_dir, 5, 120, is_playlist=True)
        self.assertEqual(p2.name, "005 - %(title)s.%(ext)s")

        p3 = output_path(out_dir, 1, 1, is_playlist=False)
        self.assertEqual(p3.name, "%(title)s.%(ext)s")

    def test_already_downloaded_playlist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            self.assertIsNone(already_downloaded(out_dir, 1, 10, is_playlist=True))

            f1 = out_dir / "01 - Test Video.mp4"
            f1.write_text("dummy video content")
            self.assertEqual(already_downloaded(out_dir, 1, 10, is_playlist=True), f1)
            self.assertEqual(already_downloaded(out_dir, 1, 150, is_playlist=True), f1)

            part_file = out_dir / "03 - Incomplete.mp4.part"
            part_file.write_text("partial")
            self.assertIsNone(already_downloaded(out_dir, 3, 10, is_playlist=True))

            empty_file = out_dir / "05 - Empty.mp4"
            empty_file.touch()
            self.assertIsNone(already_downloaded(out_dir, 5, 10, is_playlist=True))

    def test_already_downloaded_single_video(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            entry = {"title": "Cool Single Video", "id": "xyz123"}
            self.assertIsNone(already_downloaded(out_dir, is_playlist=False, entry=entry))

            f = out_dir / "Cool Single Video.mp4"
            f.write_text("video bytes")
            self.assertEqual(already_downloaded(out_dir, is_playlist=False, entry=entry), f)

    def test_already_downloaded_audio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            entry = {"title": "My Podcast Episode", "id": "audio123"}

            f_mp3 = out_dir / "01 - My Podcast Episode.mp3"
            f_mp3.write_text("mp3 bytes")
            self.assertEqual(
                already_downloaded(out_dir, 1, 10, is_playlist=True, is_audio=True),
                f_mp3,
            )

    def test_playlist_folder_sanitization(self):
        title = "My Playlist: Tips / Tricks & Hacks? <2026>"
        sanitized = sanitize_filename(title, restricted=False)
        self.assertNotIn("/", sanitized)
        self.assertNotIn("?", sanitized)
        self.assertNotIn("<", sanitized)
        self.assertNotIn(">", sanitized)

    def test_youtube_js_opts(self):
        opts = youtube_js_opts()
        self.assertIsInstance(opts, dict)
        self.assertIn("http_chunk_size", opts)
        clients = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", [])
        self.assertNotIn("android", clients)

    def test_build_download_opts_audio(self):
        opts = build_download_opts(
            Path("/tmp/downloads"),
            "audio",
            index=1,
            total=5,
            has_ffmpeg=True,
        )
        self.assertTrue(opts.get("writethumbnail"))
        pps = opts.get("postprocessors", [])
        keys = [p["key"] for p in pps]
        self.assertIn("FFmpegExtractAudio", keys)
        self.assertIn("FFmpegMetadata", keys)
        self.assertIn("EmbedThumbnail", keys)

    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(512), "512 B")
        self.assertEqual(format_size(1024), "1.0 KB")
        self.assertEqual(format_size(1024 * 1024 * 15.5), "15.5 MB")
        self.assertEqual(format_size(1024 * 1024 * 1024 * 2.3), "2.3 GB")

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(125), "2m 5s")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(7320), "2h 2m")


class TestDownloadJob(unittest.TestCase):
    def test_job_initialization(self):
        job = DownloadJob(
            job_id="test1",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            quality="1080",
            skip_existing=True,
            embed_thumbnail=True,
        )
        self.assertEqual(job.job_id, "test1")
        self.assertEqual(job.quality, "1080")
        self.assertEqual(job.quality_val, 1080)
        self.assertFalse(job.is_audio)
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(job.overall_progress(), 0.0)

    def test_audio_job(self):
        job = DownloadJob(
            job_id="test2",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            quality="audio",
        )
        self.assertTrue(job.is_audio)
        self.assertEqual(job.quality_val, "audio")

    def test_overall_progress_calculation(self):
        job = DownloadJob(job_id="test3", url="https://example.com")
        job.total_items = 4
        job.completed_items = 2
        job.status = JobStatus.DOWNLOADING
        job.item_progress = 50.0  # half of the 3rd item

        # (2 + 0.5) / 4 = 2.5 / 4 = 62.5%
        self.assertEqual(job.overall_progress(), 62.5)

        job.status = JobStatus.COMPLETED
        self.assertEqual(job.overall_progress(), 100.0)


class TestJobManager(unittest.TestCase):
    def test_enqueue_and_cancel(self):
        manager = JobManager(max_concurrent=0)  # No active workers
        job = manager.enqueue_job("https://youtube.com/watch?v=xyz", quality="720")
        self.assertIsNotNone(job.job_id)
        self.assertEqual(manager.get_job(job.job_id), job)

        jobs = manager.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_id"], job.job_id)

        # Test cancel
        cancelled = manager.cancel_job(job.job_id)
        self.assertTrue(cancelled)
        self.assertEqual(job.status, JobStatus.CANCELLED)

    def test_cancel_while_downloading(self):
        manager = JobManager(max_concurrent=0)
        job = manager.enqueue_job("https://youtube.com/watch?v=xyz")
        job.status = JobStatus.DOWNLOADING
        job.speed_str = "5 MB/s"

        cancelled = manager.cancel_job(job.job_id)
        self.assertTrue(cancelled)
        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertEqual(job.speed_str, "0 B/s")
        self.assertEqual(job.phase, "Cancelled by user")
        self.assertIsNotNone(job.finished_at)

    def test_pause_and_resume(self):
        manager = JobManager(max_concurrent=0)
        job = manager.enqueue_job("https://youtube.com/watch?v=xyz")
        job.status = JobStatus.DOWNLOADING

        # Pause
        self.assertTrue(manager.pause_job(job.job_id))
        self.assertEqual(job.status, JobStatus.PAUSED)
        self.assertFalse(job.pause_event.is_set())

        # Resume
        self.assertTrue(manager.resume_job(job.job_id))
        self.assertEqual(job.status, JobStatus.DOWNLOADING)
        self.assertTrue(job.pause_event.is_set())

    @patch("ytget_server.fetch_info")
    def test_job_execution_cancelled(self, mock_fetch):
        mock_fetch.return_value = (
            False,
            "Sample Video",
            [{"id": "123", "title": "Sample Video", "webpage_url": "https://youtube.com/watch?v=123"}],
        )
        manager = JobManager(max_concurrent=0)
        job = manager.enqueue_job("https://youtube.com/watch?v=123")

        def fake_download(*args, **kwargs):
            job.cancel()
            return False

        with patch.object(manager, "_download_single_item", side_effect=fake_download):
            manager._execute_job(job)

        self.assertEqual(job.status, JobStatus.CANCELLED)
        self.assertEqual(job.phase, "Cancelled by user")

    @patch("ytget_server.fetch_info")
    def test_job_execution_success(self, mock_fetch):
        mock_fetch.return_value = (
            False,
            "Sample Video",
            [{"id": "123", "title": "Sample Video", "webpage_url": "https://youtube.com/watch?v=123"}],
        )
        manager = JobManager(max_concurrent=0)
        job = manager.enqueue_job("https://youtube.com/watch?v=123")

        with patch.object(manager, "_download_single_item", return_value=True):
            manager._execute_job(job)

        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.title, "Sample Video")
        self.assertEqual(job.completed_items, 1)

    def test_job_manager_concurrency_scaling(self):
        manager = JobManager(max_concurrent=2, auto_start=True)
        self.assertEqual(manager.max_concurrent, 2)
        self.assertEqual(len(manager._worker_threads), 2)

        # Scale up to 4
        manager.set_max_concurrent(4)
        self.assertEqual(manager.max_concurrent, 4)
        self.assertEqual(len(manager._worker_threads), 4)

        # Scale down to 1
        manager.set_max_concurrent(1)
        self.assertEqual(manager.max_concurrent, 1)
        # Give worker threads a moment to wake up and retire
        time.sleep(0.1)
        self.assertLessEqual(len(manager._worker_threads), 2)

        manager.stop()
        self.assertEqual(len(manager._worker_threads), 0)


class TestServerEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ytget_server.job_manager.stop()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), YtGetRequestHandler)
        cls.port = cls.server.server_port
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, path: str, method: str = "GET", data: dict | None = None) -> tuple[int, dict, dict]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method=method)
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            req.add_header("Content-Type", "application/json")
            req.data = body

        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                headers = dict(resp.headers)
                resp_body = json.loads(resp.read().decode("utf-8")) if resp.headers.get("Content-Type", "").startswith("application/json") else {}
                return status, headers, resp_body
        except urllib.error.HTTPError as err:
            status = err.code
            headers = dict(err.headers)
            try:
                resp_body = json.loads(err.read().decode("utf-8"))
            except Exception:
                resp_body = {}
            return status, headers, resp_body

    def test_cors_options(self):
        req = urllib.request.Request(f"{self.base_url}/status", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 204)
            self.assertIn("Access-Control-Allow-Origin", resp.headers)
            self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")

    def test_status_endpoint(self):
        status, headers, body = self._request("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "ok")
        self.assertEqual(body.get("service"), "ytget")
        self.assertIn("Access-Control-Allow-Origin", headers)

    def test_download_endpoint_validation(self):
        # Empty body
        status, _, body = self._request("/download", method="POST", data={})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

        # Valid body
        status, _, body = self._request("/download", method="POST", data={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "quality": "720",
        })
        self.assertEqual(status, 202)
        self.assertEqual(body.get("status"), "accepted")
        self.assertIn("job_id", body)

        job_id = body["job_id"]
        # Cancel test job
        status, _, cancel_body = self._request(f"/cancel/{job_id}", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(cancel_body.get("status"), "cancelled")

    def test_pause_and_resume_endpoints(self):
        status, _, body = self._request("/download", method="POST", data={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        })
        self.assertEqual(status, 202)
        job_id = body["job_id"]

        # Pause
        status, _, p_body = self._request(f"/pause/{job_id}", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(p_body.get("status"), "paused")

        # Resume
        status, _, r_body = self._request(f"/resume/{job_id}", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(r_body.get("status"), "resumed")

        # Cancel
        status, _, c_body = self._request(f"/cancel/{job_id}", method="POST")
        self.assertEqual(status, 200)

    def test_tasks_list_and_detail(self):
        status, _, body = self._request("/tasks")
        self.assertEqual(status, 200)
        self.assertIn("jobs", body)

    def test_downloads_endpoint(self):
        status, _, body = self._request("/downloads")
        self.assertEqual(status, 200)
        self.assertIn("downloads", body)

    def test_settings_endpoints(self):
        # GET /settings
        status, _, body = self._request("/settings")
        self.assertEqual(status, 200)
        self.assertIn("max_concurrent", body)

        # POST /settings
        status, _, post_body = self._request("/settings", method="POST", data={"max_concurrent": 12})
        self.assertEqual(status, 200)
        self.assertEqual(post_body.get("status"), "ok")
        self.assertEqual(post_body.get("max_concurrent"), 12)

        # Verify GET reflects update
        status, _, get_body = self._request("/settings")
        self.assertEqual(status, 200)
        self.assertEqual(get_body.get("max_concurrent"), 12)

        # Reset back to 10
        self._request("/settings", method="POST", data={"max_concurrent": 10})

    @patch.object(ytget_server.yt_dlp.YoutubeDL, "extract_info")
    def test_probe_url_filters_unavailable_qualities_and_shows_sizes(self, mock_extract):
        # Simulate an older video with only 360p and 240p
        mock_extract.return_value = {
            "id": "old_vid_123",
            "title": "Classic Old Clip",
            "duration": 180,
            "_type": "video",
            "formats": [
                {"format_id": "18", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize": 15000000},
                {"format_id": "133", "height": 240, "vcodec": "avc1", "acodec": "none", "filesize": 6000000},
                {"format_id": "140", "height": None, "vcodec": "none", "acodec": "mp4a", "filesize": 2500000},
            ],
        }

        from ytget_server import probe_url
        data = probe_url("https://www.youtube.com/watch?v=old_vid_123")
        self.assertEqual(data["id"], "old_vid_123")
        self.assertEqual(data["available_heights"], [360, 240])

        quality_ids = [q["id"] for q in data["qualities"]]
        self.assertIn("best", quality_ids)
        self.assertIn("360", quality_ids)
        self.assertIn("240", quality_ids)
        self.assertIn("audio", quality_ids)
        # MUST NOT contain qualities that this video doesn't have!
        self.assertNotIn("1080", quality_ids)
        self.assertNotIn("720", quality_ids)
        self.assertNotIn("480", quality_ids)

        # Verify sizes are calculated and formatted
        for q in data["qualities"]:
            self.assertTrue(len(q["size_str"]) > 0, f"size_str missing for {q['id']}")

    @patch("ytget_server.probe_url")
    def test_probe_endpoints_get_and_post(self, mock_probe):
        mock_probe.return_value = {
            "id": "test_vid",
            "title": "Mock Video",
            "qualities": [{"id": "1080", "label": "1080p Full HD", "size_str": "95.2 MB"}],
        }
        # GET /probe
        status, _, body = self._request("/probe?url=https://www.youtube.com/watch?v=test_vid")
        self.assertEqual(status, 200)
        self.assertEqual(body.get("status"), "ok")
        self.assertEqual(body.get("id"), "test_vid")
        self.assertEqual(body["qualities"][0]["size_str"], "95.2 MB")

        # POST /probe
        status, _, post_body = self._request("/probe", method="POST", data={"url": "https://www.youtube.com/watch?v=test_vid"})
        self.assertEqual(status, 200)
        self.assertEqual(post_body.get("status"), "ok")
        self.assertEqual(post_body.get("id"), "test_vid")


if __name__ == "__main__":
    unittest.main()
