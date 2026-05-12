import unittest

from guardian.resilience import CircuitBreaker, CircuitOpenError, CircuitState, RetryPolicy


class ResiliencePrimitiveTests(unittest.TestCase):
    def test_retry_policy_backoff_is_capped(self):
        policy = RetryPolicy(max_retries=3, base_delay_seconds=0.1, max_delay_seconds=0.25, backoff_multiplier=2)
        self.assertEqual(policy.delay_for_attempt(1), 0.1)
        self.assertEqual(policy.delay_for_attempt(2), 0.2)
        self.assertEqual(policy.delay_for_attempt(3), 0.25)

    def test_circuit_opens_after_threshold_and_recovers_half_open(self):
        breaker = CircuitBreaker(name="fake", failure_threshold=2, recovery_seconds=0)
        breaker.record_failure("one")
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        breaker.record_failure("two")
        self.assertEqual(breaker.state, CircuitState.OPEN)
        self.assertTrue(breaker.allow_request())
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)
        breaker.record_success()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertEqual(breaker.consecutive_failures, 0)

    def test_open_circuit_rejects_before_recovery_window(self):
        breaker = CircuitBreaker(name="fake", failure_threshold=1, recovery_seconds=999)
        breaker.record_failure("boom")
        with self.assertRaises(CircuitOpenError):
            breaker.assert_request_allowed()


if __name__ == "__main__":
    unittest.main()
