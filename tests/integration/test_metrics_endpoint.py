import pytest


class TestMetricsEndpoint:
    async def test_returns_200_without_auth(self, anon_client):
        """GET /metrics does NOT require authentication."""
        response = await anon_client.get("/metrics")
        assert response.status_code == 200

    async def test_returns_text_plain(self, anon_client):
        """GET /metrics returns text/plain content type."""
        response = await anon_client.get("/metrics")
        assert "text/plain" in response.headers["content-type"]

    async def test_prometheus_format(self, anon_client):
        """GET /metrics returns valid Prometheus exposition format."""
        response = await anon_client.get("/metrics")
        body = response.text

        # Must contain HELP and TYPE lines
        assert "# HELP vault_inference_requests_per_minute" in body
        assert "# TYPE vault_inference_requests_per_minute gauge" in body
        assert "# HELP vault_inference_avg_latency_ms" in body
        assert "# TYPE vault_inference_avg_latency_ms gauge" in body
        assert "# HELP vault_inference_tokens_per_second" in body
        assert "# TYPE vault_inference_tokens_per_second gauge" in body
        assert "# HELP vault_inference_active_requests" in body
        assert "# TYPE vault_inference_active_requests gauge" in body

    async def test_contains_metric_values(self, anon_client):
        """GET /metrics has metric lines with numeric values."""
        response = await anon_client.get("/metrics")
        body = response.text

        # Each metric name should appear as a value line (not just HELP/TYPE)
        for metric in [
            "vault_inference_requests_per_minute",
            "vault_inference_avg_latency_ms",
            "vault_inference_tokens_per_second",
            "vault_inference_active_requests",
        ]:
            # Find lines that start with the metric name (not HELP/TYPE)
            value_lines = [
                line for line in body.splitlines()
                if line.startswith(metric) and not line.startswith("#")
            ]
            assert len(value_lines) == 1, f"Expected exactly 1 value line for {metric}"

            # Value should be a number
            parts = value_lines[0].split()
            assert len(parts) == 2
            float(parts[1])  # Should not raise

    async def test_zero_values_with_no_traffic(self, anon_client):
        """GET /metrics returns 0 values when no inference traffic exists."""
        response = await anon_client.get("/metrics")
        body = response.text

        assert "vault_inference_requests_per_minute 0.0" in body
        assert "vault_inference_avg_latency_ms 0.0" in body
        assert "vault_inference_tokens_per_second 0.0" in body
        assert "vault_inference_active_requests 0" in body
